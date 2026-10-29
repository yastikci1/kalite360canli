import os
import time
import json
import re
import requests
import streamlit as st
from datetime import datetime
from db import db, get_complaint
from services.mail_service import send_complaint_email

OLLAMA_BASE_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
DEFAULT_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")

# Anahtarınızda açık olan gerçek birincil model
DEFAULT_GEMINI_MODEL = "gemini-3-flash-preview"

# Google v1beta üzerinde 404 veren veya yeni kullanıcılara kapatılan eski model isimleri
INVALID_OR_DEPRECATED_MODELS = {
    "gemini-3.0-flash",
    "gemini-3.0-pro",
    "gemini-2.5-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-2.0-flash-exp",
}

LAST_AI_ERROR = ""
_CACHED_GEMINI_MODELS = {}


def get_last_ai_error() -> str:
    return LAST_AI_ERROR


def _clean_api_key(raw_key: str) -> str:
    if not raw_key:
        return ""
    return str(raw_key).strip().strip('"').strip("'").replace(" ", "")


def _sanitize_gemini_model_name(model_name: str) -> str:
    m = str(model_name or "").strip().replace("models/", "")
    if not m or m in INVALID_OR_DEPRECATED_MODELS:
        return DEFAULT_GEMINI_MODEL
    return m


def _auto_update_db_gemini_model(new_model: str):
    """Geçersiz model adını veritabanında otomatik olarak çalışan modelle günceller."""
    try:
        with db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO company_comm_settings (key, value) VALUES ('ai_gemini_model', ?)",
                (new_model,)
            )
            conn.commit()
    except Exception:
        pass


def _get_saved_ai_config() -> dict:
    """Veritabanında ve aktif Streamlit oturumunda kayıtlı AI ayarlarını okur."""
    cfg = {
        "ai_provider": os.getenv("KALITE360_AI_PROVIDER", "auto"),
        "gemini_api_key": _clean_api_key(os.getenv("GEMINI_API_KEY", "")),
        "gemini_model": _sanitize_gemini_model_name(os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)),
        "openai_api_key": _clean_api_key(os.getenv("OPENAI_API_KEY", "")),
        "openai_base_url": os.getenv("OPENAI_BASE_URL", "https://api.groq.com/openai/v1").strip(),
        "openai_model": os.getenv("OPENAI_MODEL", "llama-3.3-70b-versatile").strip(),
        "ollama_model": os.getenv("OLLAMA_MODEL", "").strip(),
    }
    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT key, value FROM company_comm_settings WHERE key LIKE 'ai_%'"
            ).fetchall()
            for r in rows:
                k = r["key"].replace("ai_", "", 1)
                if r["value"]:
                    val = str(r["value"]).strip()
                    if "key" in k:
                        cfg[k] = _clean_api_key(val)
                    elif k == "gemini_model":
                        clean_m = _sanitize_gemini_model_name(val)
                        cfg[k] = clean_m
                        if clean_m != val:
                            _auto_update_db_gemini_model(clean_m)
                    else:
                        cfg[k] = val
    except Exception:
        pass

    try:
        ui_gem = _clean_api_key(st.session_state.get("ui_gem_key") or "")
        if ui_gem:
            cfg["gemini_api_key"] = ui_gem
        ui_gem_mdl = str(st.session_state.get("ui_gem_mdl_select") or "").strip()
        if ui_gem_mdl:
            cfg["gemini_model"] = _sanitize_gemini_model_name(ui_gem_mdl)
        ui_prov = str(st.session_state.get("ui_ai_provider_sel") or "").strip()
        if ui_prov in ["auto", "gemini", "openai_compat", "ollama"]:
            cfg["ai_provider"] = ui_prov
        ui_oa = _clean_api_key(st.session_state.get("ui_oa_key") or "")
        if ui_oa:
            cfg["openai_api_key"] = ui_oa
    except Exception:
        pass

    return cfg


def fetch_available_gemini_models(api_key: str) -> tuple[list[str], str]:
    """
    Google ModelService.ListModels uç noktasını çağırarak bu API anahtarında
    gerçekten açık olan modelleri döndürür.
    """
    clean_key = _clean_api_key(api_key)
    if not clean_key:
        return [], "API anahtarı boş."

    if clean_key in _CACHED_GEMINI_MODELS and _CACHED_GEMINI_MODELS[clean_key]:
        return _CACHED_GEMINI_MODELS[clean_key], ""

    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={clean_key}"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code != 200:
            try:
                err_msg = res.json().get("error", {}).get("message", f"HTTP {res.status_code}")
            except Exception:
                err_msg = f"HTTP {res.status_code}"
            return [], err_msg

        data = res.json()
        supported = []
        for m in data.get("models", []):
            methods = m.get("supportedGenerationMethods", [])
            name = str(m.get("name", "")).replace("models/", "")
            if not name or name in INVALID_OR_DEPRECATED_MODELS:
                continue
            if any(x in name for x in [
                "embedding", "imagen", "veo", "aqa", "tts", "audio",
                "robotics", "computer-use", "learnlm", "nano"
            ]):
                continue
            if not methods or "generateContent" in methods:
                supported.append(name)

        # Anahtarınızda açık ve en stabil çalışan modellerin öncelik sırası
        priority_order = [
            "gemini-3-flash-preview",
            "gemma-4-31b-it",
            "gemma-4-26b-a4b-it",
            "gemini-flash-lite-latest",
            "gemini-2.5-flash-lite",
            "gemini-flash-latest",
            "gemini-2.5-pro",
            "gemini-pro-latest",
        ]
        sorted_models = []
        for pref in priority_order:
            if pref in supported and pref not in sorted_models:
                sorted_models.append(pref)
        for s in supported:
            if s not in sorted_models:
                sorted_models.append(s)

        if sorted_models:
            _CACHED_GEMINI_MODELS[clean_key] = sorted_models
        return sorted_models, ""
    except Exception as exc:
        return [], f"Bağlantı hatası: {exc}"


def test_gemini_connection(api_key: str, preferred_model: str = DEFAULT_GEMINI_MODEL) -> tuple[bool, str, list[str]]:
    """Ayarlar panelindeki 'Bağlantıyı Test Et' butonu için canlı test yapar."""
    clean_key = _clean_api_key(api_key)
    if not clean_key:
        return False, "Lütfen önce geçerli bir Gemini API Anahtarı girin.", []

    models, err = fetch_available_gemini_models(clean_key)
    if err:
        return False, f"API Anahtarı doğrulanamadı: {err}", []

    target_mdl = _sanitize_gemini_model_name(preferred_model)
    if models and target_mdl not in models:
        target_mdl = models[0]

    test_out = _call_gemini_api(
        messages=[{"role": "user", "content": "Sadece 'Kalite360 Yapay Zeka Aktif!' yaz."}],
        api_key=clean_key,
        model_name=target_mdl,
        temperature=0.0,
        timeout=20
    )
    if test_out:
        return True, f"({target_mdl}) Yanıt: '{test_out[:80]}'", models
    return False, f"Model yanıt vermedi: {LAST_AI_ERROR}", models


def get_ollama_status() -> dict:
    """Ollama servisinin ve Bulut AI (Gemini / Groq / OpenAI) motorunun durumunu kontrol eder."""
    cfg = _get_saved_ai_config()
    ollama_available = False
    models = []
    chosen_ollama = None

    try:
        res = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=0.8)
        if res.status_code == 200:
            data = res.json()
            models = [m.get("name") for m in data.get("models", []) if m.get("name")]
            if models:
                ollama_available = True
                preferred_order = [
                    "qwen2.5:14b", "qwen2.5:7b", "gemma2:9b", "llama3.1:8b",
                    "qwen2.5", "gemma2", "llama3", "mistral", "deepseek"
                ]
                chosen_ollama = models[0]
                if cfg.get("ollama_model") and cfg["ollama_model"] in models:
                    chosen_ollama = cfg["ollama_model"]
                else:
                    for pref in preferred_order:
                        matched = [m for m in models if pref in m.lower()]
                        if matched:
                            chosen_ollama = matched[0]
                            break
    except Exception:
        pass

    has_gemini = bool(cfg.get("gemini_api_key", "").strip())
    has_openai = bool(cfg.get("openai_api_key", "").strip())
    provider = cfg.get("ai_provider", "auto")

    active_engine = "NLP"
    active_model_label = "Yerleşik NLP"
    is_any_llm_ready = False

    if provider == "gemini" and has_gemini:
        active_engine = "Gemini"
        active_model_label = cfg.get("gemini_model", DEFAULT_GEMINI_MODEL)
        is_any_llm_ready = True
    elif provider == "openai_compat" and has_openai:
        active_engine = "Cloud API"
        active_model_label = cfg.get("openai_model", "llama-3.3-70b-versatile")
        is_any_llm_ready = True
    elif provider == "ollama" and ollama_available:
        active_engine = "Ollama"
        active_model_label = chosen_ollama
        is_any_llm_ready = True
    else:
        if has_gemini:
            active_engine = "Gemini"
            active_model_label = cfg.get("gemini_model", DEFAULT_GEMINI_MODEL)
            is_any_llm_ready = True
        elif has_openai:
            active_engine = "Cloud API"
            active_model_label = cfg.get("openai_model", "llama-3.3-70b-versatile")
            is_any_llm_ready = True
        elif ollama_available:
            active_engine = "Ollama"
            active_model_label = chosen_ollama
            is_any_llm_ready = True

    return {
        "available": is_any_llm_ready,
        "ollama_available": ollama_available,
        "models": models,
        "active_model": active_model_label,
        "active_engine": active_engine,
        "config": cfg,
    }


def _normalize_gemini_contents(messages: list) -> tuple[str, list]:
    system_instruction = ""
    raw_turns = []

    for m in messages:
        role = m.get("role", "user")
        text = str(m.get("content", "")).strip()
        if not text:
            continue
        if role == "system":
            system_instruction += text + "\n\n"
        elif role == "assistant":
            raw_turns.append({"role": "model", "text": text})
        else:
            raw_turns.append({"role": "user", "text": text})

    while raw_turns and raw_turns[0]["role"] == "model":
        raw_turns.pop(0)

    merged_contents = []
    for turn in raw_turns:
        if merged_contents and merged_contents[-1]["role"] == turn["role"]:
            merged_contents[-1]["parts"][0]["text"] += "\n\n" + turn["text"]
        else:
            merged_contents.append({
                "role": turn["role"],
                "parts": [{"text": turn["text"]}]
            })

    return system_instruction.strip(), merged_contents


def _extract_gemini_text(data: dict) -> str | None:
    candidates = data.get("candidates", [])
    if not candidates:
        return None
    content_obj = candidates[0].get("content", {})
    parts = content_obj.get("parts", [])
    if not parts:
        return None
    non_thought_texts = [p.get("text", "") for p in parts if not p.get("thought", False) and p.get("text")]
    if non_thought_texts:
        return "".join(non_thought_texts).strip()
    all_texts = [p.get("text", "") for p in parts if p.get("text")]
    return "".join(all_texts).strip() or None


def _call_gemini_api(
    messages: list,
    api_key: str,
    model_name: str,
    temperature: float = 0.2,
    timeout: int = 45,
    json_mode: bool = False
) -> str | None:
    """
    Sadece ListModels içinde gerçekten var olan modellere istek gönderir (404 hatasını tamamen engeller).
    429 / 503 yoğunluk durumunda otomatik olarak sıradaki açık modele (örn: gemma-4-31b-it / gemma-4-26b-a4b-it) geçer.
    """
    global LAST_AI_ERROR
    LAST_AI_ERROR = ""

    clean_key = _clean_api_key(api_key)
    if not clean_key:
        LAST_AI_ERROR = "Gemini API anahtarı girilmemiş."
        return None

    system_instruction, contents = _normalize_gemini_contents(messages)
    if not contents:
        return None

    clean_model = _sanitize_gemini_model_name(model_name)
    discovered_models, list_err = fetch_available_gemini_models(clean_key)

    if list_err and ("api key" in list_err.lower() or "400" in list_err or "403" in list_err):
        LAST_AI_ERROR = f"Geçersiz API Anahtarı: {list_err}"
        return None

    # SADECE anahtarda gerçekten açık olan modelleri aday listesine al (404 NotFound'u %100 önler)
    candidate_models = []
    if discovered_models:
        if clean_model in discovered_models:
            candidate_models.append(clean_model)
        for dm in discovered_models:
            if dm not in candidate_models and dm not in INVALID_OR_DEPRECATED_MODELS:
                candidate_models.append(dm)
    else:
        for fallback_m in [
            clean_model,
            "gemini-3-flash-preview",
            "gemma-4-31b-it",
            "gemma-4-26b-a4b-it",
            "gemini-flash-lite-latest",
        ]:
            if fallback_m and fallback_m not in candidate_models and fallback_m not in INVALID_OR_DEPRECATED_MODELS:
                candidate_models.append(fallback_m)

    combined_prompt = (f"[SİSTEM TALİMATI]:\n{system_instruction}\n\n" if system_instruction else "") + "\n\n".join(
        f"[{'KULLANICI' if c['role'] == 'user' else 'ASİSTAN'}]:\n{c['parts'][0]['text']}" for c in contents
    )

    gen_cfg = {
        "temperature": temperature,
        "maxOutputTokens": 8192,
    }
    if json_mode:
        gen_cfg["responseMimeType"] = "application/json"

    payload = {
        "contents": [{"role": "user", "parts": [{"text": combined_prompt}]}],
        "generationConfig": gen_cfg,
    }

    errors_log = []
    for mdl in candidate_models[:5]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{mdl}:generateContent?key={clean_key}"
        try:
            res = requests.post(url, json=payload, timeout=timeout)
            if res.status_code == 200:
                text_out = _extract_gemini_text(res.json())
                if text_out:
                    LAST_AI_ERROR = ""
                    if mdl != clean_model:
                        _auto_update_db_gemini_model(mdl)
                    return text_out
                else:
                    errors_log.append(f"{mdl}: Boş yanıt döndü")
            elif res.status_code in [429, 503]:
                # Kota veya anlık sunucu yoğunluğu varsa 1 saniye bekle ve hemen sıradaki açık modele geç
                errors_log.append(f"{mdl}: Yoğunluk/Kota (HTTP {res.status_code})")
                time.sleep(1.0)
                continue
            else:
                try:
                    err_detail = res.json().get("error", {}).get("message", f"HTTP {res.status_code}")
                except Exception:
                    err_detail = f"HTTP {res.status_code}"

                # Eğer yeni kullanıcı kısıtlamasıysa (örn. gemini-2.5-* modelleri) bunu kalıcı kara listeye al ve sıradakine geç
                if "no longer available" in err_detail.lower() or res.status_code == 404:
                    INVALID_OR_DEPRECATED_MODELS.add(mdl)
                    continue

                errors_log.append(f"{mdl}: {err_detail[:120]}")
                if res.status_code in [400, 401, 403] and "api key" in err_detail.lower():
                    LAST_AI_ERROR = f"Geçersiz API Anahtarı: {err_detail}"
                    return None
        except Exception as exc:
            errors_log.append(f"{mdl}: {exc}")

    LAST_AI_ERROR = " | ".join(errors_log[:2]) if errors_log else "Gemini API yanıt döndüremedi."
    return None


def _call_openai_compat_api(
    messages: list,
    api_key: str,
    base_url: str,
    model_name: str,
    temperature: float = 0.2,
    timeout: int = 30
) -> str | None:
    global LAST_AI_ERROR
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {_clean_api_key(api_key)}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model_name.strip(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 3000
    }
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=timeout)
        if res.status_code == 200:
            data = res.json()
            choices = data.get("choices", [])
            if choices:
                LAST_AI_ERROR = ""
                return choices[0].get("message", {}).get("content", "").strip() or None
        else:
            LAST_AI_ERROR = f"Cloud API HTTP {res.status_code}: {res.text[:160]}"
    except Exception as exc:
        LAST_AI_ERROR = f"Cloud API Hatası: {exc}"
    return None


def query_ollama_chat(
    messages: list,
    model: str = None,
    temperature: float = 0.2,
    timeout: int = 45,
    json_mode: bool = False
) -> str | None:
    status = get_ollama_status()
    if not status["available"]:
        return None

    cfg = status["config"]
    engine = status["active_engine"]

    if engine == "Gemini" and cfg.get("gemini_api_key"):
        out = _call_gemini_api(
            messages=messages,
            api_key=cfg["gemini_api_key"],
            model_name=cfg.get("gemini_model") or DEFAULT_GEMINI_MODEL,
            temperature=temperature,
            timeout=timeout,
            json_mode=json_mode
        )
        if out:
            return out

    if engine == "Cloud API" and cfg.get("openai_api_key"):
        out = _call_openai_compat_api(
            messages=messages,
            api_key=cfg["openai_api_key"],
            base_url=cfg.get("openai_base_url") or "https://api.groq.com/openai/v1",
            model_name=cfg.get("openai_model") or "llama-3.3-70b-versatile",
            temperature=temperature,
            timeout=timeout
        )
        if out:
            return out

    if status["ollama_available"]:
        target_model = model or status["active_model"] or DEFAULT_OLLAMA_MODEL
        if target_model not in status["models"] and status["models"]:
            target_model = status["models"][0]
        try:
            payload = {
                "model": target_model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_ctx": 8192,
                }
            }
            if json_mode:
                payload["format"] = "json"

            res = requests.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=timeout)
            if res.status_code == 200:
                content = res.json().get("message", {}).get("content", "").strip()
                if content:
                    return content
        except Exception:
            return None
    return None


def classify_validity(row):
    sol = str(row.get("action_plan") or row.get("Üretilen Çözüm") or "").lower()
    findings = str(row.get("findings") or "").lower()
    full_text = f"{sol} {findings}"

    if not sol or "henüz girilmemiş" in sol or sol.strip() in ["-", "çözüm bekleniyor", "teknik servis incelemesi / çözüm bekleniyor"]:
        return "Çözüm Belirtilmemiş"

    if any(k in full_text for k in [
        "confirmed", "duplicated", "replaced", "repaired", "worn", "broken", "damaged",
        "overheating detected", "device was overheating", "bearing",
        "leakage test failure", "corroded", "stripped", "demagnetized", "error code",
        "failure", "noted that", "defective", "doğrulandı", "onarılmıştır", "arızalı"
    ]):
        return "Şikâyet Doğru"

    if any(k in full_text for k in [
        "report not confirmed", "not confirmed", "could not confirm", "unconfirmed",
        "no defect", "no problem", "no failure", "within specification", "passed all tests",
        "operated as intended", "could not reproduce", "could not able to reproduce",
        "temperature was measured to be", "tested and the temperature was measured",
        "hata bulunamadı", "asılsız"
    ]):
        return "Şikâyet Yanlış"

    if any(k in full_text for k in [
        "no conclusion can be drawn. no evaluation was performed, as the device was not returned",
        "evaluation anticipated", "under evaluation", "under investigation", "investigation ongoing",
        "inceleme bekleniyor"
    ]):
        return "İnceleme Yapılıyor"

    return "İnceleme Yapılıyor"


def extract_temperature_from_text(text):
    match = re.search(r'(\d+(?:\.\d+)?)\s*(?:°|deg|degrees?)?\s*(f|c)\b', str(text), re.IGNORECASE)
    if match:
        return f"{match.group(1)}°{match.group(2).upper()}"
    return None


def analyze_with_llm_json(complaint_text, solution_text):
    prompt = f"""You are a medical device quality engineer.
Analyze the following FDA MAUDE complaint and manufacturer evaluation text.
Extract the key findings into a STRICT JSON object.

[COMPLAINT]: {complaint_text}
[EVALUATION]: {solution_text}

JSON STRUCTURE REQUIREMENTS:
{{
    "is_evaluated": true or false,
    "is_defect_confirmed": true or false,
    "max_temperature_f": float or null,
    "identified_damages_tr": [list of strings]
}}"""
    try:
        raw_out = query_ollama_chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            timeout=15,
            json_mode=True
        )
        if raw_out:
            m_json = re.search(r'\{.*\}', raw_out, re.S)
            return json.loads(m_json.group(0) if m_json else raw_out)
    except Exception:
        pass
    return None


def ensure_email_read_tracking():
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS email_ingest(
            mailbox TEXT, folder TEXT, uid TEXT, complaint_id INTEGER, imported_at TEXT, viewed_at TEXT,
            PRIMARY KEY(mailbox,folder,uid))""")
        cols = [row[1] for row in conn.execute("PRAGMA table_info(email_ingest)").fetchall()]
        if "viewed_at" not in cols:
            conn.execute("ALTER TABLE email_ingest ADD COLUMN viewed_at TEXT")


def mark_email_complaint_viewed(complaint_id):
    ensure_email_read_tracking()
    with db() as conn:
        conn.execute(
            "UPDATE email_ingest SET viewed_at=COALESCE(viewed_at, ?) WHERE complaint_id=?",
            (datetime.now().isoformat(timespec="seconds"), int(complaint_id))
        )


def email_complaint_states():
    ensure_email_read_tracking()
    with db() as conn:
        rows = conn.execute("SELECT complaint_id, MIN(viewed_at) AS viewed_at FROM email_ingest GROUP BY complaint_id").fetchall()
    return {int(r["complaint_id"]): (r["viewed_at"] is not None) for r in rows}


def notify_new_complaint(cid):
    item = get_complaint(cid)
    if not item:
        return
    staff_email = os.getenv("QUALITY_NOTIFICATION_EMAIL", "").strip()
    if staff_email:
        send_complaint_email(
            staff_email,
            f"Yeni şikâyet atandı: {item['complaint_no']}",
            f"Şikâyet: {item['complaint_no']}\nMüşteri: {item['customer']}\nÜrün: {item['product']}\nÖncelik: {item['priority']}\nSorumlu: {item['owner']}\nAçıklama: {item['description']}"
        )
    if item.get("email"):
        send_complaint_email(
            item["email"],
            f"Şikâyetiniz kaydedildi: {item['complaint_no']}",
            f"Merhaba {item['contact'] or item['customer']},\n\nŞikâyetiniz kaydedilmiştir. Referans numaranız: {item['complaint_no']}.\n\nSaygılarımızla"
        )