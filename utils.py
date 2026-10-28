import os
import json
import re
import requests
from datetime import datetime
from db import db, get_complaint
from services.mail_service import send_complaint_email

OLLAMA_BASE_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
DEFAULT_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")


def _get_saved_ai_config() -> dict:
    """Veritabanında kayıtlı olan AI sağlayıcı (Ollama / Gemini / Groq / OpenAI) ayarlarını okur."""
    cfg = {
        "ai_provider": os.getenv("KALITE360_AI_PROVIDER", "auto"),  # auto, ollama, gemini, openai_compat
        "gemini_api_key": os.getenv("GEMINI_API_KEY", ""),
        "gemini_model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
        "openai_base_url": os.getenv("OPENAI_BASE_URL", "https://api.groq.com/openai/v1"),
        "openai_model": os.getenv("OPENAI_MODEL", "llama-3.3-70b-versatile"),
        "ollama_model": os.getenv("OLLAMA_MODEL", ""),
    }
    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT key, value FROM company_comm_settings WHERE key LIKE 'ai_%'"
            ).fetchall()
            for r in rows:
                k = r["key"].replace("ai_", "", 1)
                if r["value"]:
                    cfg[k] = r["value"]
    except Exception:
        pass
    return cfg


def get_ollama_status() -> dict:
    """Ollama servisinin ve varsa Bulut AI (Gemini / Groq / OpenAI) motorunun durumunu kontrol eder."""
    cfg = _get_saved_ai_config()
    ollama_available = False
    models = []
    chosen_ollama = None

    try:
        res = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=1.5)
        if res.status_code == 200:
            data = res.json()
            models = [m.get("name") for m in data.get("models", []) if m.get("name")]
            if models:
                ollama_available = True
                # Daha güçlü Türkçe modelleri (7b, 9b, 14b) önceliklendir
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
        active_model_label = cfg.get("gemini_model", "gemini-2.5-flash")
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
        # Otomatik seçim: Bulut API varsa önce onu, yoksa yerel Ollama'yı seç
        if has_gemini:
            active_engine = "Gemini"
            active_model_label = cfg.get("gemini_model", "gemini-2.5-flash")
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


def _call_gemini_api(messages: list, api_key: str, model_name: str, temperature: float = 0.2, timeout: int = 25) -> str | None:
    """Google Gemini REST API üzerinden yüksek zekâlı yanıt üretir."""
    system_instruction = ""
    contents = []
    for m in messages:
        role = m.get("role", "user")
        text = str(m.get("content", "")).strip()
        if not text:
            continue
        if role == "system":
            system_instruction += text + "\n\n"
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": text}]})
        else:
            contents.append({"role": "user", "parts": [{"text": text}]})

    if not contents:
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key.strip()}"
    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": 2048,
        }
    }
    if system_instruction.strip():
        payload["systemInstruction"] = {"parts": [{"text": system_instruction.strip()}]}

    res = requests.post(url, json=payload, timeout=timeout)
    if res.status_code == 200:
        data = res.json()
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts).strip() or None
    return None


def _call_openai_compat_api(messages: list, api_key: str, base_url: str, model_name: str, temperature: float = 0.2, timeout: int = 25) -> str | None:
    """Groq, OpenAI, DeepSeek veya OpenRouter uyumlu REST API çağrısı yapar."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model_name.strip(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 2048
    }
    res = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if res.status_code == 200:
        data = res.json()
        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "").strip() or None
    return None


def query_ollama_chat(messages: list, model: str = None, temperature: float = 0.2, timeout: int = 30, json_mode: bool = False) -> str | None:
    """
    Seçili AI motoru (Gemini API, Groq/OpenAI API veya Yerel Ollama) üzerinden
    çok adımlı sohbet veya JSON çıktısı üretir.
    """
    status = get_ollama_status()
    if not status["available"]:
        return None

    cfg = status["config"]
    engine = status["active_engine"]

    # 1. Google Gemini API
    if engine == "Gemini" and cfg.get("gemini_api_key"):
        try:
            out = _call_gemini_api(
                messages=messages,
                api_key=cfg["gemini_api_key"],
                model_name=cfg.get("gemini_model") or "gemini-2.5-flash",
                temperature=temperature,
                timeout=timeout
            )
            if out:
                return out
        except Exception:
            pass

    # 2. Groq / OpenAI / DeepSeek Uyumlu API
    if engine == "Cloud API" and cfg.get("openai_api_key"):
        try:
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
        except Exception:
            pass

    # 3. Yerel Ollama Servisi
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

            prompt_parts = []
            for m in messages:
                role_lbl = m.get("role", "user").upper()
                prompt_parts.append(f"[{role_lbl}]:\n{m.get('content', '')}")
            prompt_parts.append("[ASSISTANT]:")
            gen_payload = {
                "model": target_model,
                "prompt": "\n\n".join(prompt_parts),
                "stream": False,
                "options": {"temperature": temperature}
            }
            if json_mode:
                gen_payload["format"] = "json"
            res_gen = requests.post(f"{OLLAMA_BASE_URL}/api/generate", json=gen_payload, timeout=timeout)
            if res_gen.status_code == 200:
                return res_gen.json().get("response", "").strip() or None
        except Exception:
            return None
    return None


def classify_validity(row):
    sol = str(row.get("action_plan") or row.get("Üretilen Çözüm") or "").lower()
    findings = str(row.get("findings") or "").lower()
    full_text = f"{sol} {findings}"
    
    if not sol or "henüz girilmemiş" in sol or sol.strip() in ["-", "çözüm bekleniyor", "teknik servis incelemesi / çözüm bekleniyor"]:
        return "Çözüm Belirtilmemiş"

    # 1. ÖNCELİK: Kesin Arıza / Teknik Bulgusal Tespiti (Şikayet Doğru)
    if any(k in full_text for k in [
        "confirmed", "duplicated", "replaced", "repaired", "worn", "broken", "damaged", 
        "overheating detected", "device was overheating", "bearing", 
        "leakage test failure", "corroded", "stripped", "demagnetized", "error code", 
        "failure", "noted that", "defective", "doğrulandı", "onarılmıştır", "arızalı"
    ]):
        return "Şikâyet Doğru"
        
    # 2. ÖNCELİK: Kesin Sağlam / Doğrulanamadı Tespiti (Şikayet Yanlış / Hata Yok)
    if any(k in full_text for k in [
        "report not confirmed", "not confirmed", "could not confirm", "unconfirmed", 
        "no defect", "no problem", "no failure", "within specification", "passed all tests", 
        "operated as intended", "could not reproduce", "could not able to reproduce", 
        "temperature was measured to be", "tested and the temperature was measured",
        "hata bulunamadı", "asılsız"
    ]):
        return "Şikâyet Yanlış"
        
    # 3. ÖNCELİK: Gerçekten Hiç İncelenmemiş / Bekleyen Vakalar (İnceleme Yapılıyor)
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
    "is_evaluated": true or false (false ONLY if device was not returned or evaluation not performed),
    "is_defect_confirmed": true or false (true if actual damage/fault was found, false if "no defect", "could not confirm", or "within specification"),
    "max_temperature_f": float or null (extract temperature number if mentioned, else null),
    "identified_damages_tr": [list of strings] (Briefly list found damages in TURKISH. e.g., ["korozyon", "stator gevşemesi", "derinlik testi başarısız"]. Empty list if no damage)
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
        conn.execute("UPDATE email_ingest SET viewed_at=COALESCE(viewed_at, ?) WHERE complaint_id=?", (datetime.now().isoformat(timespec="seconds"), int(complaint_id)))


def email_complaint_states():
    ensure_email_read_tracking()
    with db() as conn:
        rows = conn.execute("SELECT complaint_id, MIN(viewed_at) AS viewed_at FROM email_ingest GROUP BY complaint_id").fetchall()
    return {int(r["complaint_id"]): (r["viewed_at"] is not None) for r in rows}


def notify_new_complaint(cid):
    item = get_complaint(cid)
    if not item: return
    staff_email = os.getenv("QUALITY_NOTIFICATION_EMAIL", "").strip()
    if staff_email:
        send_complaint_email(staff_email, f"Yeni şikâyet atandı: {item['complaint_no']}", f"Şikâyet: {item['complaint_no']}\nMüşteri: {item['customer']}\nÜrün: {item['product']}\nÖncelik: {item['priority']}\nSorumlu: {item['owner']}\nAçıklama: {item['description']}")
    if item.get("email"):
        send_complaint_email(item["email"], f"Şikâyetiniz kaydedildi: {item['complaint_no']}", f"Merhaba {item['contact'] or item['customer']},\n\nŞikâyetiniz kaydedilmiştir. Referans numaranız: {item['complaint_no']}.\n\nSaygılarımızla")