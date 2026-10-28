import json
import re
import difflib
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from datetime import datetime, timedelta
from db import complaints, db, get_complaint, actions, communications, log
from ai_quality_engine import detect_and_handle_quality_query, render_quality_engine_tab

try:
    from utils import get_ollama_status, query_ollama_chat
except ImportError:
    def get_ollama_status():
        return {"available": False, "ollama_available": False, "models": [], "active_model": None, "active_engine": "NLP", "config": {}}
    def query_ollama_chat(*args, **kwargs):
        return None

# --- SABİT SEÇENEKLER VE KURUMSAL VARSAYILANLAR ---
RESPONSE_PURPOSES = [
    "Otomatik (Şikâyet Durumuna Göre)",
    "1. Yeni Şikâyet (Alındı Bilgilendirmesi)",
    "2. İnceleme Devam Ediyor (Ara Bilgilendirme)",
    "3. Ek Bilgi Bekleniyor (Teknik Bilgi / Fotoğraf Talebi)",
    "4. Kök Neden Belirlendi (Teknik Bulgu Açıklaması)",
    "5. Çözüm Önerildi (Onarım / Değişim / İşlem Planı)",
    "6. Düzeltici Faaliyet Başlatıldı (DÖF / CAPA Bilgilendirmesi)",
    "7. İnceleme Gecikti (Özür ve Durum Bilgilendirmesi)",
    "8. Şikâyet Çözüldü (Çözüm Özeti ve Memnuniyet Teyidi)",
    "9. Şikâyet Kapatılmak Üzere (Kapanış Onayı Talebi)",
]

CHANNELS = ["E-posta", "Kısa Mesaj (SMS)", "Genel Müşteri Mesajı"]
TONES = ["Kurumsal", "Empatik", "Teknik", "Kısa ve Net", "Ayrıntılı"]
LENGTHS = ["Kısa", "Standart", "Detaylı"]

AUTHORIZED_ROLES = ["Müşteri Hizmetleri", "Kalite", "Yönetici"]

DEFAULT_COMPANY_SETTINGS = {
    "company_name": "Kalite360 Fabrika & Medikal Sistemler A.Ş.",
    "department": "Müşteri Hizmetleri ve Kalite Güvence Departmanı",
    "greeting_template": "Sayın {muhatap},",
    "closing_template": "Saygılarımızla,\nKalite360 Kalite Güvence ve Müşteri İletişim Ekibi",
    "contact_info": "E-posta: kalite@kalite360.com | Tel: +90 (850) 360 00 00",
    "policy_note": "Onaylanmamış garanti, iade, değişim, tazminat veya kesin teslim tarihi taahhüdü verilmez."
}


# --- 1. VERİTABANI TABLOLARI (Sohbet Geçmişi, Taslaklar ve Kurumsal Ayarlar) ---
def init_assistant_tables():
    with db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS ai_chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                role TEXT,
                content TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS response_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                complaint_id INTEGER,
                complaint_no TEXT,
                purpose TEXT,
                channel TEXT,
                tone TEXT,
                length_opt TEXT,
                extra_instruction TEXT,
                subject TEXT,
                draft_text TEXT,
                source_summary TEXT,
                created_by TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS company_comm_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        for k, v in DEFAULT_COMPANY_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO company_comm_settings (key, value) VALUES (?, ?)", (k, v))
        conn.commit()


def get_company_settings() -> dict:
    init_assistant_tables()
    with db() as conn:
        rows = conn.execute("SELECT key, value FROM company_comm_settings").fetchall()
    res = dict(DEFAULT_COMPANY_SETTINGS)
    for r in rows:
        res[r["key"]] = r["value"]
    return res


def save_company_settings(new_vals: dict):
    init_assistant_tables()
    with db() as conn:
        for k, v in new_vals.items():
            conn.execute("INSERT OR REPLACE INTO company_comm_settings (key, value) VALUES (?, ?)", (k, str(v)))
        conn.commit()


def load_user_chat_history(username: str):
    init_assistant_tables()
    with db() as conn:
        rows = conn.execute(
            "SELECT role, content FROM ai_chat_history WHERE username=? ORDER BY id ASC LIMIT 50",
            (username,)
        ).fetchall()
    if rows:
        return [{"role": r["role"], "content": r["content"]} for r in rows]
    return [
        {
            "role": "assistant",
            "content": (
                "Merhaba! 👋 Ben **Kalite360 Yapay Zekâ Asistanı**.\n\n"
                "- Benimle tıpkı bir **Kalite Güvence Mühendisi** arkadaşınla konuşur gibi doğal dille yazışabilirsin.\n"
                "- Veritabanındaki tüm şikâyetleri, ürünleri, müşterileri, **6M kök nedenlerini** ve **DÖF/8D aksiyonlarını** senin soruna göre anlık sorgulayıp analiz ederim."
            )
        }
    ]


def save_chat_message(username: str, role: str, content: str):
    init_assistant_tables()
    with db() as conn:
        conn.execute(
            "INSERT INTO ai_chat_history (username, role, content, created_at) VALUES (?, ?, ?, ?)",
            (username, role, content, datetime.now().isoformat(timespec="seconds"))
        )
        conn.commit()


def clear_user_chat_history(username: str):
    init_assistant_tables()
    with db() as conn:
        conn.execute("DELETE FROM ai_chat_history WHERE username=?", (username,))
        conn.commit()


def save_draft_to_db(cid: int, c_no: str, purpose: str, channel: str, tone: str, length_opt: str,
                     extra_inst: str, subject: str, body_text: str, source_summary: dict, username: str):
    init_assistant_tables()
    now = datetime.now().isoformat(timespec="seconds")
    src_json = json.dumps(source_summary, ensure_ascii=False)
    with db() as conn:
        cur = conn.execute("""
            INSERT INTO response_drafts
            (complaint_id, complaint_no, purpose, channel, tone, length_opt, extra_instruction,
             subject, draft_text, source_summary, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (int(cid), c_no, purpose, channel, tone, length_opt, extra_inst,
              subject, body_text, src_json, username, now, now))
        draft_id = cur.lastrowid
        conn.execute(
            "INSERT INTO contacts(complaint_id, channel, outcome, notes, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (int(cid), channel, "Taslak (Gönderilmedi)", f"[{purpose} | {tone}] {body_text[:400]}", username, now)
        )
        log(conn, int(cid), username, "Müşteri geri dönüş taslağı kaydedildi", f"Taslak #{draft_id} ({channel} - {purpose})")
        conn.commit()
    return draft_id


def get_saved_drafts(cid: int):
    init_assistant_tables()
    with db() as conn:
        rows = conn.execute(
            "SELECT id, purpose, channel, tone, length_opt, draft_text, created_by, created_at "
            "FROM response_drafts WHERE complaint_id=? ORDER BY id DESC LIMIT 10",
            (int(cid),)
        ).fetchall()
    return [dict(r) for r in rows]


# --- 2. YETKİLENDİRME VE GÜVENLİK KONTROLÜ ---
def is_user_authorized(user: dict) -> tuple[bool, str]:
    if not user or not isinstance(user, dict) or not user.get("username"):
        return False, "Müşteri bilgilerine ve taslak oluşturucuya erişmek için yetkili bir hesapla giriş yapmalısınız."
    role = user.get("role", "")
    if role not in AUTHORIZED_ROLES:
        return False, f"'{role}' rolünün müşteri iletişim bilgilerine erişim yetkisi bulunmamaktadır."
    return True, "Yetkili"


# --- 3. CSS VE SABİT YUVARLAK FLOATING WIDGET TASARIMI (BÜYÜT / KÜÇÜLT DESTEKLİ) ---
def inject_custom_css(is_fullscreen: bool = False):
    if is_fullscreen:
        popover_pos_css = """
        div[data-baseweb="popover"] {
            position: fixed !important;
            top: 10px !important;
            left: 10px !important;
            right: 10px !important;
            bottom: 90px !important;
            transform: none !important;
            width: calc(100vw - 20px) !important;
            height: calc(100vh - 100px) !important;
            z-index: 999999 !important;
        }

        div[data-baseweb="popover"] > div,
        div[data-testid="stPopoverBody"] {
            background-color: #ffffff !important;
            background: #ffffff !important;
            color: #0f172a !important;
            width: calc(100vw - 20px) !important;
            min-width: calc(100vw - 20px) !important;
            max-width: calc(100vw - 20px) !important;
            height: calc(100vh - 100px) !important;
            max-height: calc(100vh - 100px) !important;
            border-radius: 18px !important;
            border: 1px solid #cbd5e1 !important;
            box-shadow: 0 22px 55px rgba(15, 23, 42, 0.35), 0 4px 14px rgba(15, 23, 42, 0.18) !important;
            padding: 18px 26px !important;
            overflow-y: auto !important;
            backdrop-filter: none !important;
        }
        """
    else:
        popover_pos_css = """
        div[data-baseweb="popover"] {
            z-index: 999999 !important;
        }

        div[data-baseweb="popover"] > div,
        div[data-testid="stPopoverBody"] {
            background-color: #ffffff !important;
            background: #ffffff !important;
            color: #0f172a !important;
            width: 50vw !important;
            min-width: 560px !important;
            max-width: calc(100vw - 32px) !important;
            height: 84vh !important;
            max-height: calc(100vh - 60px) !important;
            border-radius: 18px !important;
            border: 1px solid #cbd5e1 !important;
            box-shadow: 0 22px 55px rgba(15, 23, 42, 0.30), 0 4px 14px rgba(15, 23, 42, 0.14) !important;
            padding: 16px 20px !important;
            overflow-y: auto !important;
            backdrop-filter: none !important;
        }
        """

    st.markdown(f"""
    <style>
        div.element-container:has(div[data-testid="stPopover"]),
        div[data-testid="stElementContainer"]:has(div[data-testid="stPopover"]) {{
            position: fixed !important;
            bottom: 24px !important;
            right: 24px !important;
            width: 64px !important;
            height: 64px !important;
            z-index: 999990 !important;
            margin: 0 !important;
            padding: 0 !important;
        }}

        div[data-testid="stPopover"] {{
            position: fixed !important;
            bottom: 24px !important;
            right: 24px !important;
            width: 64px !important;
            height: 64px !important;
            z-index: 999990 !important;
            margin: 0 !important;
            padding: 0 !important;
        }}

        div[data-testid="stPopover"] > div {{
            width: 64px !important;
            height: 64px !important;
        }}

        div[data-testid="stPopover"] button,
        button[data-testid="stPopoverButton"] {{
            width: 64px !important;
            min-width: 64px !important;
            max-width: 64px !important;
            height: 64px !important;
            min-height: 64px !important;
            max-height: 64px !important;
            border-radius: 50% !important;
            background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%) !important;
            color: #ffffff !important;
            border: 2px solid #ffffff !important;
            box-shadow: 0 8px 24px rgba(37, 99, 235, 0.45), 0 2px 6px rgba(0, 0, 0, 0.2) !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            padding: 0 !important;
            margin: 0 !important;
            cursor: pointer !important;
            transition: transform 0.2s ease, box-shadow 0.2s ease !important;
        }}

        div[data-testid="stPopover"] button:hover,
        button[data-testid="stPopoverButton"]:hover {{
            transform: scale(1.08) !important;
            background: linear-gradient(135deg, #1d4ed8 0%, #1e3a8a 100%) !important;
            box-shadow: 0 12px 28px rgba(37, 99, 235, 0.6) !important;
        }}

        div[data-testid="stPopover"] button p,
        button[data-testid="stPopoverButton"] p {{
            font-size: 28px !important;
            line-height: 1 !important;
            margin: 0 !important;
            padding: 0 !important;
        }}

        div[data-testid="stPopover"] button svg,
        button[data-testid="stPopoverButton"] svg {{
            display: none !important;
        }}

        {popover_pos_css}

        div[data-testid="stPopoverBody"] .stButton > button {{
            border-radius: 8px !important;
            font-size: 12px !important;
            font-weight: 600 !important;
            padding: 5px 8px !important;
            min-height: 32px !important;
            height: auto !important;
            background-color: #f8fafc !important;
            color: #1e293b !important;
            border: 1px solid #cbd5e1 !important;
            width: 100% !important;
        }}

        div[data-testid="stPopoverBody"] .stButton > button:hover {{
            background-color: #eff6ff !important;
            border-color: #2563eb !important;
            color: #1d4ed8 !important;
        }}

        div[data-testid="stPopoverBody"] div[data-testid="stChatMessage"] {{
            background-color: #f8fafc !important;
            border: 1px solid #e2e8f0 !important;
            border-radius: 12px !important;
            padding: 10px 12px !important;
            margin-bottom: 8px !important;
        }}
    </style>
    """, unsafe_allow_html=True)


# --- 4. ŞİKÂYET VERİSİ ÇÖZÜMLEME VE DOĞRULAMA MOTORU ---
def _extract_cause(text):
    t = str(text or "").lower()
    if any(k in t for k in ["overheat", "hot", "ısınma", "isinma", "temperature", "sıcaklık", "sicaklik"]): return "Aşırı Isınma / Termal Sapma"
    if any(k in t for k in ["bearing", "worn", "aşınma", "asinma", "rulman", "bilya"]): return "Rulman / Mekanik Aşınma"
    if any(k in t for k in ["stuck", "collet", "sıkışma", "sikisma", "kilitlen", "pens"]): return "Mekanik Sıkışma / Pens Kilitlenmesi"
    if any(k in t for k in ["cable", "wire", "kablo", "temassız", "temassiz"]): return "Kablo / Bağlantı Temassızlığı"
    if any(k in t for k in ["corrosion", "corroded", "oksit", "pas"]): return "Korozyon / Oksitlenme"
    if any(k in t for k in ["leak", "hipot", "shock", "kaçak", "kacak", "elektrik"]): return "Elektriksel Kaçak / İzolasyon"
    if any(k in t for k in ["stop", "not work", "çalışmı", "calismi", "durdu"]): return "Ani Durma / Fonksiyon Kaybı"
    if any(k in t for k in ["noise", "ses", "rough", "titreşim", "titresim"]): return "Anormal Ses / Titreşim"
    if any(k in t for k in ["break", "broken", "cracked", "kırık", "kirik", "çatlak", "catlak", "koptu"]): return "Fiziksel Kırılma / Çatlak"
    if any(k in t for k in ["tolerans", "ölçü", "olcu", "çap", "cap", "boyut"]): return "Ölçü / Tolerans Uygunsuzluğu"
    if any(k in t for k in ["teslim", "gecik", "paket", "ambalaj"]): return "Sevkiyat / Paketleme Problemi"
    return "Teknik Performans Bildirimi"


def _is_meaningful_field(val: str, ignore_phrases=None) -> bool:
    s = str(val or "").strip()
    if not s or s in ["-", "None", "null", "Belirtilmemiş"]:
        return False
    low = s.lower()
    default_ignores = [
        "henüz girilmemiş", "belirtilmemiş", "çözüm bekleniyor",
        "no evaluation was performed", "no conclusion can be drawn",
        "fda maude kaydında olay metni belirtilmemiş"
    ]
    if ignore_phrases:
        default_ignores.extend(ignore_phrases)
    return not any(p in low for p in default_ignores)


def inspect_complaint_context(item: dict) -> dict:
    cid = int(item.get("id", 0))
    c_no = str(item.get("complaint_no") or f"ID-{cid}")
    customer = str(item.get("customer") or "").strip()
    contact = str(item.get("contact") or "").strip()
    phone = str(item.get("phone") or "").strip()
    email = str(item.get("email") or "").strip()
    product = str(item.get("product") or "").strip()
    order_no = str(item.get("order_no") or "").strip()
    category = str(item.get("category") or "Genel").strip()
    priority = str(item.get("priority") or "Orta").strip()
    status = str(item.get("status") or "Yeni").strip()
    validity = str(item.get("validity") or "Değerlendirilmedi").strip()
    urgent = bool(item.get("urgent_return"))
    sentiment = float(item.get("sentiment_score") or 0.0)

    tr_desc = str(item.get("tr_description") or "").strip()
    raw_desc = str(item.get("description") or "").strip()
    desc_text = tr_desc if _is_meaningful_field(tr_desc) else raw_desc

    tr_plan = str(item.get("tr_action_plan") or "").strip()
    raw_plan = str(item.get("action_plan") or "").strip()
    plan_text = tr_plan if _is_meaningful_field(tr_plan) else raw_plan

    findings_text = str(item.get("findings") or "").strip()
    root_cause_text = str(item.get("root_cause") or "").strip()
    action_type_text = str(item.get("action_type") or "").strip()
    requested_text = str(item.get("requested") or "").strip()

    act_df = actions(cid) if cid else pd.DataFrame()
    comm_df = communications(cid) if cid else pd.DataFrame()

    open_actions = []
    if not act_df.empty:
        for _, r in act_df.iterrows():
            open_actions.append(f"{r.get('title')} (Sorumlu: {r.get('assignee')}, Durum: {r.get('status')}, Termin: {r.get('due_date')})")

    prev_contacts_count = len(comm_df) if not comm_df.empty else 0

    created_str = str(item.get("created_at") or "")[:10]
    elapsed_days = 0
    try:
        c_dt = pd.to_datetime(item.get("created_at"), format="mixed", errors="coerce")
        if pd.notna(c_dt):
            elapsed_days = max(0, (datetime.now() - c_dt.to_pydatetime().replace(tzinfo=None)).days)
    except Exception:
        elapsed_days = 0

    has_order_no = _is_meaningful_field(order_no)
    has_findings = _is_meaningful_field(findings_text)
    has_root_cause = _is_meaningful_field(root_cause_text)
    has_action_plan = _is_meaningful_field(plan_text)
    has_contact_info = _is_meaningful_field(email) or _is_meaningful_field(phone)

    corp_keywords = ["a.ş", "ltd", "inc", "corp", "hastane", "hospital", "medical", "medikal", "klinik", "sanayi", "makina", "otomotiv", "grup", "firma", "maude"]
    is_corporate = any(k in customer.lower() for k in corp_keywords) or bool(contact and customer and contact != customer)

    missing_fields = []
    clarifying_questions = []
    if not has_order_no:
        missing_fields.append("Lot / Seri / Sipariş Numarası (order_no)")
        clarifying_questions.append("Müşteriden ürünün seri/lot numarasını veya sipariş kodunu talep etmek ister misiniz?")
    if not has_root_cause:
        missing_fields.append("Doğrulanmış Kök Neden (root_cause)")
    if not has_action_plan:
        missing_fields.append("Teknik Servis Çözüm / Aksiyon Planı (action_plan)")
        clarifying_questions.append("Henüz veritabanına onaylı bir çözüm planı girilmemiş; müşteriye incelemenin sürdüğünü mü bildirelim?")
    if not has_contact_info:
        missing_fields.append("Müşteri E-posta / Telefon Bilgisi")

    is_delayed = (status != "Kapalı" and elapsed_days > 10)
    if status == "Yeni":
        auto_purpose = RESPONSE_PURPOSES[1]
    elif status == "Kapalı":
        auto_purpose = RESPONSE_PURPOSES[8]
    elif status == "Doğrulama":
        auto_purpose = RESPONSE_PURPOSES[9]
    elif is_delayed:
        auto_purpose = RESPONSE_PURPOSES[7]
    elif not has_order_no and status in ["Yeni", "İncelemede"]:
        auto_purpose = RESPONSE_PURPOSES[3]
    elif open_actions:
        auto_purpose = RESPONSE_PURPOSES[6]
    elif has_action_plan and status == "Aksiyon Bekliyor":
        auto_purpose = RESPONSE_PURPOSES[5]
    elif has_root_cause:
        auto_purpose = RESPONSE_PURPOSES[4]
    else:
        auto_purpose = RESPONSE_PURPOSES[2]

    verified_issue_topic = _extract_cause(desc_text)
    verified_findings_clean = ""
    if has_findings:
        if "Olay Türü:" in findings_text and "|" in findings_text:
            verified_findings_clean = f"Teknik kayıt sınıflandırması: {findings_text}"
        else:
            verified_findings_clean = findings_text

    return {
        "id": cid,
        "complaint_no": c_no,
        "customer": customer or "Değerli Müşterimiz",
        "contact": contact,
        "phone": phone or "-",
        "email": email or "-",
        "product": product or "İlgili Ürün",
        "order_no": order_no if has_order_no else "",
        "category": category,
        "priority": priority,
        "status": status,
        "validity": validity,
        "urgent": urgent,
        "sentiment": sentiment,
        "is_corporate": is_corporate,
        "created_date": created_str or "-",
        "elapsed_days": elapsed_days,
        "is_delayed": is_delayed,
        "issue_topic": verified_issue_topic,
        "description": desc_text[:500] if _is_meaningful_field(desc_text) else "",
        "requested": requested_text if _is_meaningful_field(requested_text) else "",
        "findings": verified_findings_clean,
        "root_cause": root_cause_text if has_root_cause else "",
        "action_type": action_type_text if _is_meaningful_field(action_type_text, ["inceleme gerekli"]) else "",
        "action_plan": plan_text[:600] if has_action_plan else "",
        "open_actions": open_actions,
        "prev_contacts_count": prev_contacts_count,
        "has_order_no": has_order_no,
        "has_findings": has_findings,
        "has_root_cause": has_root_cause,
        "has_action_plan": has_action_plan,
        "missing_fields": missing_fields,
        "clarifying_questions": clarifying_questions,
        "auto_purpose": auto_purpose,
    }


# --- 5. MÜŞTERİ GERİ DÖNÜŞ METNİ ÜRETİM MOTORU ---
def generate_customer_response_draft(
    item: dict,
    purpose: str = "Otomatik (Şikâyet Durumuna Göre)",
    channel: str = "E-posta",
    tone: str = "Kurumsal",
    length_opt: str = "Standart",
    extra_instruction: str = "",
    use_llm: bool = False
) -> dict:
    ctx = inspect_complaint_context(item)
    comp_cfg = get_company_settings()

    resolved_purpose = ctx["auto_purpose"] if purpose.startswith("Otomatik") else purpose
    guardrail_warnings = []

    if ("8. Şikâyet Çözüldü" in resolved_purpose or "9. Şikâyet Kapatılmak" in resolved_purpose) and ctx["status"] not in ["Kapalı", "Doğrulama"] and not ctx["has_action_plan"]:
        guardrail_warnings.append(
            f"⚠️ **Durum Uyarısı:** Şikâyet durumu şu an **`{ctx['status']}`** ve veritabanında henüz tamamlanmış bir çözüm (`action_plan`) kayıtlı değil. "
            "Şikâyet sonuçlanmamış olduğu için metin **kesinleşmiş çözüm uydurulmadan**, mevcut doğrulama/kapanış teyidi çerçevesinde hazırlandı."
        )

    if "4. Kök Neden" in resolved_purpose and not ctx["has_root_cause"] and not ctx["has_findings"]:
        guardrail_warnings.append(
            "⚠️ **Eksik Veri Uyarısı:** Veritabanında doğrulanmış bir `root_cause` (kök neden) veya teknik bulgu kaydı bulunmuyor. "
            "Doğrulanmamış teknik sonuçları müşteriye kesin bilgi olarak aktarmamak adına metinde **ön teknik değerlendirme bulguları** ifadesi kullanıldı."
        )

    if "5. Çözüm Önerildi" in resolved_purpose and not ctx["has_action_plan"] and not ctx["action_type"]:
        guardrail_warnings.append(
            "⚠️ **Eksik Çözüm Kaydı:** Veritabanında henüz onaylanmış bir `action_plan` bulunmuyor. "
            "Şirket politikası gereği onaylanmamış değişim/iade/garanti taahhüdü verilmeden standart teknik değerlendirme adımları aktarıldı."
        )

    if ctx["contact"] and ctx["customer"] and ctx["contact"].lower() != ctx["customer"].lower():
        muhatap = f"{ctx['customer']} Yetkilisi Sayın {ctx['contact']}" if ctx["is_corporate"] else f"Sayın {ctx['contact']}"
    else:
        muhatap = f"{ctx['customer']} Yetkilisi" if ctx["is_corporate"] else f"Sayın {ctx['customer']}"

    greeting = f"{muhatap},"
    if "Sayın" not in greeting:
        greeting = f"Sayın {greeting}"

    order_suffix_tech = f" (Seri/Lot/MDR No: {ctx['order_no']})" if ctx["has_order_no"] else ""
    order_suffix_corp = f" (Ref/Lot: {ctx['order_no']})" if ctx["has_order_no"] else ""

    is_sensitive = ctx["priority"] in ["Kritik", "Yüksek"] or ctx["urgent"] or ctx["sentiment"] <= -0.35 or tone == "Empatik"

    if tone == "Empatik" or (is_sensitive and tone not in ["Kısa ve Net", "Teknik"]):
        empathy_opening = (
            f"Öncelikle **{ctx['product']}** ürünümüzün kullanımı sırasında yaşadığınız **{ctx['issue_topic']}** "
            f"kaynaklı aksaklık ve operasyonel süreçlerinizde oluşan kesinti nedeniyle içtenlikle üzüntü duyduğumuzu belirtmek isteriz. "
            f"Bildiriminiz ekibimiz tarafından **{ctx['priority']}** öncelik seviyesinde yakın takibe alınmıştır."
        )
    elif tone == "Teknik":
        empathy_opening = (
            f"**{ctx['product']}**{order_suffix_tech} ürününe ilişkin "
            f"**{ctx['complaint_no']}** referans numarasıyla kayıt altına alınan **{ctx['issue_topic']}** konulu teknik bildiriminiz "
            f"Kalite Güvence ve Mühendislik birimlerimizce incelenmektedir."
        )
    elif tone == "Kısa ve Net":
        empathy_opening = (
            f"**{ctx['complaint_no']}** referanslı **{ctx['product']}** ({ctx['issue_topic']}) bildiriminiz hakkında güncel durum bilgisi aşağıda paylaşılmıştır."
        )
    else:
        empathy_opening = (
            f"Tarafımıza iletmiş olduğunuz **{ctx['product']}**{order_suffix_corp} "
            f"ürünümüze ait **{ctx['issue_topic']}** konulu geri bildiriminiz, **{ctx['complaint_no']}** kayıt numarası ile "
            f"Kalite Yönetim Sistemimiz kapsamında değerlendirilmektedir."
        )

    prev_comm_sentence = ""
    if ctx["prev_contacts_count"] > 0 and length_opt != "Kısa":
        prev_comm_sentence = " Önceki görüşmelerimizin devamı niteliğinde, sürecin güncel aşamasını bilgilerinize sunarız."

    prod_ref = f"{ctx['product']}" + (f" (No: {ctx['order_no']})" if ctx["has_order_no"] else "")

    if "1. Yeni Şikâyet" in resolved_purpose:
        subject = f"Şikâyet Bildiriminiz Alındı [{ctx['complaint_no']}] - {ctx['product']}"
        status_block = (
            f"Bildiriminiz **{ctx['created_date']}** tarihinde **{ctx['complaint_no']}** takip numarası ve "
            f"**{ctx['priority']}** öncelik derecesi ile sistemimize kaydedilmiştir."
        )
        action_block = (
            f"Kalite Güvence ve Teknik Servis uzmanlarımız, belirttiğiniz **{ctx['issue_topic']}** durumuyla ilgili "
            f"üretim/seri kayıtlarını ve teknik parametreleri incelemeye başlamıştır."
        )
        if ctx["has_order_no"]:
            customer_req_block = (
                "İnceleme sürecinin hızlandırılabilmesi adına, varsa olaya ilişkin ek gözlemlerinizi veya ürünü "
                "teknik inceleme için gönderim durumunuzu bu mesaj üzerinden paylaşabilirsiniz."
            )
        else:
            customer_req_block = (
                "İzlenebilirlik sorgulamasının eksiksiz yapılabilmesi için ürünün **Seri / Lot numarasını** ve varsa "
                "hatayı gösteren **fotoğraf/video kayıtlarını** tarafımıza iletmenizi rica ederiz."
            )
        next_step_block = "Ön teknik değerlendirme tamamlandığında süreç hakkında tarafınıza yeniden bilgilendirme yapılacaktır."

    elif "2. İnceleme Devam Ediyor" in resolved_purpose:
        subject = f"Teknik İnceleme Güncellemesi [{ctx['complaint_no']}] - {ctx['product']}"
        status_block = (
            f"**{ctx['complaint_no']}** numaralı başvurunuzun teknik inceleme süreci halen devam etmektedir "
            f"(Mevcut Durum: **{ctx['status']}**)."
        )
        if ctx["has_action_plan"] or ctx["has_findings"]:
            verified_note = ctx["action_plan"] or ctx["findings"]
            action_block = f"Şu ana kadar yapılan teknik değerlendirmelerde kaydedilen mevcut durum: *{verified_note[:280]}*"
        else:
            action_block = (
                f"**{prod_ref}** ürününe ait **{ctx['issue_topic']}** bildirimi kapsamında ilgili kalite kontrol, "
                f"test ve doğrulama adımları teknik ekibimizce yürütülmektedir."
            )
        customer_req_block = (
            "Şu aşamada tarafınızdan beklenen ek bir işlem bulunmamaktadır."
            if ctx["has_order_no"] else
            "Ürüne ait seri/lot numarası henüz paylaşılmadıysa iletmeniz incelemeyi hızlandıracaktır."
        )
        next_step_block = "Doğrulama testleri ve teknik servis raporu sonuçlandığında nihai bulgular tarafınızla paylaşılacaktır."

    elif "3. Ek Bilgi Bekleniyor" in resolved_purpose:
        subject = f"Teknik İnceleme İçin Ek Bilgi Talebi [{ctx['complaint_no']}] - {ctx['product']}"
        status_block = (
            f"**{ctx['complaint_no']}** numaralı **{ctx['product']}** bildiriminizle ilgili teknik kök neden "
            f"analizinin doğru ve eksiksiz tamamlanabilmesi için bazı ek bilgilere ihtiyaç duyulmuştur."
        )
        action_block = "Kalite Güvence ekibimiz mevcut bildirim metni üzerinden ön incelemeyi başlatmıştır."
        missing_prompts = []
        if not ctx["has_order_no"]:
            missing_prompts.append("- Ürün üzerindeki **Seri / Lot Numarası** veya **Sipariş/İrsaliye Kodu**")
        missing_prompts.extend([
            f"- **{ctx['issue_topic']}** durumunu gösteren net **fotoğraf veya kısa video** görüntüsü",
            "- Arızanın tespit edildiği **kullanım/operasyon koşulları** (devir, süre, sterilizasyon/ortam bilgisi)",
            "- Ürünün fiziksel inceleme için teknik servisimize gönderilip gönderilemeyeceği bilgisi"
        ])
        customer_req_block = "Lütfen aşağıdaki bilgileri bu mesaja yanıt olarak tarafımıza iletiniz:\n" + "\n".join(missing_prompts)
        next_step_block = "Talep edilen bilgiler ulaştığında teknik laboratuvar/kök neden doğrulaması derhal tamamlanacaktır."

    elif "4. Kök Neden Belirlendi" in resolved_purpose:
        subject = f"Teknik İnceleme Bulguları ve Kök Neden Bilgilendirmesi [{ctx['complaint_no']}]"
        if ctx["has_root_cause"]:
            status_block = (
                f"**{ctx['complaint_no']}** numaralı başvurunuz kapsamında yürütülen teknik inceleme sonucunda "
                f"doğrulanmış kök neden tespiti tamamlanmıştır:\n"
                f"- **Tespit Edilen Kök Neden:** {ctx['root_cause']}"
            )
        elif ctx["has_action_plan"] or ctx["has_findings"]:
            ref_info = ctx["action_plan"] or ctx["findings"]
            status_block = (
                f"**{ctx['complaint_no']}** numaralı başvurunuz kapsamında teknik servis değerlendirme bulguları kayıt altına alınmıştır:\n"
                f"- **Teknik Değerlendirme Özeti:** {ref_info[:320]}"
            )
        else:
            status_block = (
                f"**{ctx['complaint_no']}** numaralı başvurunuzda bildirilen **{ctx['issue_topic']}** durumu üzerine "
                f"ön teknik sınıflandırma yapılmış olup, kesin kök neden doğrulama kayıtları güncellenmektedir."
            )
        plan_detail_suffix = f" Detay: {ctx['action_plan'][:250]}" if ctx["has_action_plan"] else ""
        action_block = f"Uygulanan/Planlanan İşlem Türü: **{ctx['action_type'] or 'Teknik Servis Değerlendirmesi'}**.{plan_detail_suffix}"
        customer_req_block = "Bulgularla ilgili sorularınız olması halinde teknik ekibimizle iletişime geçebilirsiniz."
        next_step_block = "Belirlenen bulgular doğrultusunda uygulanacak aksiyon planı ayrıca takip edilmektedir."

    elif "5. Çözüm Önerildi" in resolved_purpose:
        subject = f"Çözüm Önerisi ve Uygulama Planı [{ctx['complaint_no']}] - {ctx['product']}"
        status_block = f"**{ctx['complaint_no']}** referanslı **{prod_ref}** bildiriminize ilişkin teknik değerlendirme aşaması tamamlanmıştır."
        if ctx["has_action_plan"] or ctx["action_type"]:
            plan_desc_str = ctx["action_plan"] if ctx["has_action_plan"] else "Kayıtlı işlem türü doğrultusunda standart servis prosedürü uygulanacaktır."
            action_block = (
                f"**Önerilen / Planlanan Çözüm İşlemi:**\n"
                f"- **İşlem Türü:** {ctx['action_type'] or 'Teknik Servis Çözümü'}\n"
                f"- **Teknik Plan / Açıklama:** {plan_desc_str}"
            )
        else:
            action_block = (
                "Şu an sistemde onaylanmış spesifik bir değişim/onarım planı kaydı bulunmadığından, "
                "cihazın yetkili teknik servisimizde fiziki kontrolden geçirilmesi sonrasında nihai işlem belirlenecektir."
            )
        customer_req_block = "Önerilen işlem planı doğrultusunda ilerlenebilmesi için onayınızı veya görüşünüzü rica ederiz."
        next_step_block = "Onayınızın ardından ilgili servis/kalite adımları ivedilikle devreye alınacaktır."

    elif "6. Düzeltici Faaliyet Başlatıldı" in resolved_purpose:
        subject = f"Düzeltici ve Önleyici Faaliyet (DÖF/CAPA) Bilgilendirmesi [{ctx['complaint_no']}]"
        status_block = (
            f"**{ctx['complaint_no']}** referanslı **{ctx['product']}** bildiriminiz doğrultusunda, benzer uygunsuzlukların "
            f"tekrarını önlemek amacıyla Kalite Güvence departmanımız tarafından düzeltici faaliyet süreci yürütülmektedir."
        )
        if ctx["open_actions"]:
            acts_str = "\n".join([f"- {a}" for a in ctx["open_actions"][:4]])
            action_block = f"**Kayıtlı Düzeltici Faaliyet / Aksiyon Adımları:**\n{acts_str}"
        elif ctx["has_action_plan"]:
            action_block = f"**Yürütülen Teknik Aksiyon:** {ctx['action_plan'][:300]}"
        else:
            action_block = (
                f"**{ctx['issue_topic']}** konusuyla ilgili proses kontrol, 6M kök neden doğrulaması ve "
                f"kalite denetim kayıtları incelenmektedir."
            )
        customer_req_block = "Süreç iç kalite prosedürlerimiz kapsamında takip edilmekte olup tarafınızdan ek bir aksiyon beklenmemektedir."
        next_step_block = "Düzeltici faaliyet etkinlik doğrulaması tamamlandığında nihai sonuç paylaşılacaktır."

    elif "7. İnceleme Gecikti" in resolved_purpose:
        subject = f"İnceleme Süreci Hakkında Bilgilendirme ve Özür [{ctx['complaint_no']}]"
        days_note = f" ({ctx['elapsed_days']} gündür açık olan)" if ctx["elapsed_days"] > 0 else ""
        status_block = (
            f"**{ctx['complaint_no']}** referans numarası ile takip edilen{days_note} **{ctx['product']}** "
            f"şikâyet kaydınızın sonuçlandırılmasında yaşanan gecikmeden dolayı içtenlikle özür dileriz."
        )
        tech_note_Current = f" Mevcut teknik not: *{ctx['action_plan'][:220]}*" if ctx["has_action_plan"] else ""
        action_block = (
            f"**Güncel Durum ({ctx['status']}):** Bildiriminize konu olan **{ctx['issue_topic']}** durumunun "
            f"tüm teknik boyutlarıyla doğrulanabilmesi ve yanıltıcı bir bulgu paylaşılmaması adına detaylı test/inceleme süreci devam etmektedir.{tech_note_Current}"
        )
        customer_req_block = "Sabrınız ve anlayışınız için teşekkür ederiz. Varsa süreçle ilgili öncelikli taleplerinizi bu mesaja yanıt olarak iletebilirsiniz."
        next_step_block = "Dosyanız önceliklendirilmiş olup, doğrulama tamamlanır tamamlanmaz tarafınıza kesin bilgi verilecektir."

    elif "8. Şikâyet Çözüldü" in resolved_purpose:
        subject = f"Şikâyet Çözüm Özeti ve Memnuniyet Teyidi [{ctx['complaint_no']}] - {ctx['product']}"
        if ctx["has_action_plan"]:
            status_block = f"**{ctx['complaint_no']}** referanslı **{prod_ref}** bildiriminize ilişkin teknik inceleme ve çözüm işlemleri tamamlanmıştır."
            action_block = (
                f"**Gerçekleştirilen Çözüm / İnceleme Özeti:**\n"
                f"- **Sonuç / İşlem:** {ctx['action_type'] or ctx['validity']}\n"
                f"- **Detay:** {ctx['action_plan'][:350]}"
            )
        else:
            status_block = f"**{ctx['complaint_no']}** referanslı **{prod_ref}** bildiriminize ilişkin değerlendirme süreci son aşamaya gelmiştir (Durum: **{ctx['status']}**)."
            action_block = "Veritabanında henüz detaylandırılmış bir teknik servis çözüm metni bulunmadığından, yapılan işlemin özeti kalite sorumlumuz tarafından eklenerek paylaşılacaktır."
        customer_req_block = "Gerçekleştirilen çözümün beklentilerinizi karşılayıp karşılamadığını ve memnuniyet durumunuzu tarafımıza bildirmenizi rica ederiz."
        next_step_block = "Geri bildiriminiz doğrultusunda kalite kaydımız güncellenecektir."

    else:
        subject = f"Şikâyet Kaydı Kapanış Teyidi [{ctx['complaint_no']}] - {ctx['product']}"
        status_block = (
            f"**{ctx['complaint_no']}** referanslı **{prod_ref}** başvurunuz kapsamında yapılan değerlendirme ve "
            f"bilgilendirmeler doğrultusunda kaydınız kapanış aşamasına (Durum: **{ctx['status']}**) ulaşmıştır."
        )
        action_block = (
            f"**Kayıtlı Çözüm Özeti:** {ctx['action_plan'][:280]}"
            if ctx["has_action_plan"] else
            f"**Kayıtlı İşlem Durumu:** {ctx['action_type'] or ctx['validity']}."
        )
        customer_req_block = (
            "Şikâyet kaydınızın kapatılması konusunda **onayınızı** veya varsa eklemek istediğiniz son görüşlerinizi "
            "bu mesaja yanıt olarak paylaşmanızı rica ederiz."
        )
        next_step_block = "Teyidinizin ardından kayıt Kalite360 sisteminde resmi olarak kapatılacaktır."

    extra_block = ""
    if extra_instruction and extra_instruction.strip():
        clean_inst = extra_instruction.strip()
        extra_block = f"\n**Ek Bilgilendirme Notu:** {clean_inst}\n"

    if channel == "Kısa Mesaj (SMS)":
        sms_summary = status_block.replace("\n", " ").replace("**", "")
        if len(sms_summary) > 160:
            sms_summary = sms_summary[:157] + "..."
        extra_sms = "Ek bilgi beklenmektedir. " if "3." in resolved_purpose else ""
        draft_text = (
            f"[{comp_cfg['company_name'][:18]}] {greeting} "
            f"{ctx['complaint_no']} nolu {ctx['product']} ({ctx['issue_topic']}) başvurunuz hk: "
            f"{sms_summary} {extra_sms}"
            f"Bilgi: {comp_cfg['contact_info']}"
        )
    elif channel == "Genel Müşteri Mesajı":
        if length_opt == "Kısa":
            draft_text = (
                f"{greeting}\n\n"
                f"{empathy_opening}\n\n"
                f"{status_block} {action_block}\n"
                f"{extra_block}\n"
                f"{customer_req_block}\n\n"
                f"{comp_cfg['closing_template']}"
            )
        else:
            draft_text = (
                f"{greeting}\n\n"
                f"{empathy_opening}{prev_comm_sentence}\n\n"
                f"**Mevcut Durum ve Bulgular:**\n{status_block}\n\n"
                f"**Yapılan / Planlanan İşlemler:**\n{action_block}\n"
                f"{extra_block}\n"
                f"**Beklenen Aksiyon / Bilgilendirme:**\n{customer_req_block}\n"
                f"{next_step_block}\n\n"
                f"{comp_cfg['closing_template']}"
            )
    else:
        if length_opt == "Kısa":
            draft_text = (
                f"Konu: {subject}\n\n"
                f"{greeting}\n\n"
                f"{empathy_opening}\n\n"
                f"{status_block}\n"
                f"{action_block}\n"
                f"{extra_block}"
                f"{customer_req_block}\n\n"
                f"{comp_cfg['closing_template']}\n"
                f"{comp_cfg['contact_info']}"
            )
        elif length_opt == "Detaylı" or tone == "Ayrıntılı":
            ref_details = (
                f"**Başvuru Özeti:**\n"
                f"- **Referans No:** {ctx['complaint_no']}\n"
                f"- **Ürün / Model:** {ctx['product']}{order_suffix_tech}\n"
                f"- **Bildirim Konusu:** {ctx['category']} / {ctx['issue_topic']}\n"
                f"- **Kayıt Tarihi & Öncelik:** {ctx['created_date']} · {ctx['priority']}\n"
            )
            draft_text = (
                f"Konu: {subject}\n\n"
                f"{greeting}\n\n"
                f"{empathy_opening}{prev_comm_sentence}\n\n"
                f"{ref_details}\n"
                f"**Mevcut İnceleme Durumu ve Doğrulanmış Bulgular:**\n{status_block}\n\n"
                f"**Yapılan / Planlanan İşlemler:**\n{action_block}\n"
                f"{extra_block}\n"
                f"**Müşterimizden Beklenen Bilgiler / Aksiyonlar:**\n{customer_req_block}\n\n"
                f"**Sonraki Bilgilendirme Planı:**\n{next_step_block}\n\n"
                f"{comp_cfg['closing_template']}\n"
                f"{comp_cfg['company_name']}\n"
                f"{comp_cfg['contact_info']}"
            )
        else:
            draft_text = (
                f"Konu: {subject}\n\n"
                f"{greeting}\n\n"
                f"{empathy_opening}{prev_comm_sentence}\n\n"
                f"{status_block}\n\n"
                f"{action_block}\n"
                f"{extra_block}\n"
                f"{customer_req_block}\n\n"
                f"{next_step_block}\n\n"
                f"{comp_cfg['closing_template']}\n"
                f"{comp_cfg['contact_info']}"
            )

    clean_plain_text = draft_text.replace("**", "")

    if use_llm:
        llm_prompt = f"""Aşağıdaki şikâyet verilerine ve şablon taslağa dayanarak müşteriye gönderilecek profesyonel bir Türkçe geri dönüş metni yaz.

[ŞİRKET KURALLARI]:
- Şirket: {comp_cfg['company_name']}
- Politika: {comp_cfg['policy_note']}
- Veritabanında olmayan kök neden veya çözüm bilgilerini KESİNLİKLE uydurma.

[ŞİKÂYET VERİLERİ]:
- Şikâyet No: {ctx['complaint_no']}
- Müşteri / Muhatap: {greeting}
- Ürün: {ctx['product']} (Seri/Lot: {ctx['order_no'] or 'Belirtilmemiş'})
- Konu: {ctx['issue_topic']}
- Mevcut Durum: {ctx['status']} | Öncelik: {ctx['priority']}
- Doğrulanmış Kök Neden: {ctx['root_cause'] or 'Henüz doğrulanmadı'}
- Kayıtlı Aksiyon Planı: {ctx['action_plan'] or 'Henüz girilmedi'}

[İSTENEN PARAMETRELER]:
- Amaç: {resolved_purpose}
- Kanal: {channel} | Üslup: {tone} | Uzunluk: {length_opt}
- Kullanıcı Ek Talimatı: {extra_instruction or 'Yok'}

[REFERANS TASLAK]:
{clean_plain_text}

Sadece müşteriye gönderilecek nihai metni yaz, başına veya sonuna açıklama ekleme:"""
        llm_out = query_ollama_chat(
            messages=[
                {"role": "system", "content": "Sen Kalite360 Kurumsal Müşteri İletişim ve Kalite Güvence Uzmanısın. Sadece istenen müşteri mesajını Türkçe olarak yazarsın."},
                {"role": "user", "content": llm_prompt}
            ],
            model=st.session_state.get("selected_ollama_model"),
            temperature=0.25
        )
        if llm_out:
            clean_plain_text = llm_out.strip()

    return {
        "subject": subject,
        "draft_text": clean_plain_text,
        "resolved_purpose": resolved_purpose,
        "channel": channel,
        "tone": tone,
        "length_opt": length_opt,
        "guardrail_warnings": guardrail_warnings,
        "context": ctx,
    }


# --- 6. SEVİYE YÜKSELTİLMİŞ AGENTIC AI (TEXT-TO-SQL + DOĞRUDAN YANIT) MOTORU ---
def _normalize_tr(text: str) -> str:
    if not text:
        return ""
    s = str(text).replace("İ", "i").replace("I", "ı").lower()
    s = s.replace("â", "a").replace("î", "i").replace("û", "u")
    return s.strip()


def _ascii_fold(text: str) -> str:
    s = _normalize_tr(text)
    tr_map = str.maketrans("çğıöşü", "cgiosu")
    return s.translate(tr_map)


def _fuzzy_has(text: str, keywords: list[str], threshold: float = 0.80) -> bool:
    q_norm = _normalize_tr(text)
    q_ascii = _ascii_fold(text)
    q_words = re.findall(r'\w+', q_ascii)

    for kw in keywords:
        kw_norm = _normalize_tr(kw)
        kw_ascii = _ascii_fold(kw)
        if kw_norm in q_norm or kw_ascii in q_ascii:
            return True
        if " " not in kw_ascii and len(kw_ascii) >= 4:
            for w in q_words:
                if len(w) >= 3 and difflib.SequenceMatcher(None, w, kw_ascii).ratio() >= threshold:
                    return True
    return False


def _detect_response_intent_from_chat(prompt: str):
    q = _normalize_tr(prompt)
    q_ascii = _ascii_fold(prompt)
    response_triggers = [
        "geri dönüş metni", "müşteri yanıt", "müşteriye yanıt", "müşteriye mesaj",
        "müşteriye mail", "müşteriye e-posta", "e-posta yaz", "mail yaz", "mail hazırla",
        "mesaj oluştur", "mesajı hazırla", "taslağı oluştur", "yanıt taslağı",
        "çözümünü müşteriye", "eksik teknik bilgileri talep", "özür mesajı",
        "özür ve bilgilendirme", "kapanış mesajı", "alındı bilgilendirmesi",
        "musteriye yaz", "musteri yanit", "geri donus metni", "mail olustur"
    ]
    if not any(t in q or t in q_ascii for t in response_triggers):
        return None

    purpose = "Otomatik (Şikâyet Durumuna Göre)"
    tone = "Kurumsal"
    channel = "E-posta"

    if _fuzzy_has(q, ["yeni şikayet", "alındı", "ilk bilgilendirme"]):
        purpose = RESPONSE_PURPOSES[1]
    elif _fuzzy_has(q, ["incelemenin devam", "devam ettiğini", "ara bilgilendirme", "sürdüğünü"]):
        purpose = RESPONSE_PURPOSES[2]
    elif _fuzzy_has(q, ["eksik", "talep eden", "fotoğraf", "seri numarası", "ek bilgi"]):
        purpose = RESPONSE_PURPOSES[3]
    elif _fuzzy_has(q, ["kök neden", "teknik bulgu"]):
        purpose = RESPONSE_PURPOSES[4]
    elif _fuzzy_has(q, ["çözüm öneri", "uygulanacak işlem", "onarım", "değişim"]):
        purpose = RESPONSE_PURPOSES[5]
    elif _fuzzy_has(q, ["düzeltici faaliyet", "döf", "capa"]):
        purpose = RESPONSE_PURPOSES[6]
    elif _fuzzy_has(q, ["gecikmiş", "gecikme", "özür"]):
        purpose = RESPONSE_PURPOSES[7]
        tone = "Empatik"
    elif _fuzzy_has(q, ["çözümünü müşteriye", "çözüldü", "memnuniyet"]):
        purpose = RESPONSE_PURPOSES[8]
    elif _fuzzy_has(q, ["kapanış", "kapatılmak"]):
        purpose = RESPONSE_PURPOSES[9]

    if _fuzzy_has(q, ["empatik", "özür", "samimi", "üzüntü"]):
        tone = "Empatik"
    elif "teknik" in q_ascii and "eksik" not in q_ascii:
        tone = "Teknik"
    elif _fuzzy_has(q, ["kısa", "özet"]):
        tone = "Kısa ve Net"

    if "sms" in q_ascii or "kisa mesaj" in q_ascii:
        channel = "Kısa Mesaj (SMS)"

    return {"purpose": purpose, "tone": tone, "channel": channel}


def _extract_explicit_complaint_from_prompt(prompt: str, df: pd.DataFrame):
    if df.empty or not prompt:
        return None, False

    m_no = re.search(r'\b((?:SK|FDA)-\d{4}-\d+|\b(?:SK|FDA)-[A-Za-z0-9-]+)\b', prompt, re.I)
    if m_no:
        code = m_no.group(1).upper()
        matched = df[df["complaint_no"].astype(str).str.upper() == code]
        if not matched.empty:
            return get_complaint(int(matched.iloc[0]["id"])), True

    q_ascii = _ascii_fold(prompt)
    m_id = re.search(r'(?:#|id\s*[:=-]?\s*)(\d+)\b|\b(\d+)\s*(?:nolu|numarali|id\'?li)\s*(?:sikayet|kayit)', q_ascii)
    if m_id:
        cid_val = int(m_id.group(1) or m_id.group(2))
        matched_id = df[df["id"].astype(int) == cid_val]
        if not matched_id.empty:
            return get_complaint(cid_val), True

    context_phrases = [
        "bu şikayet", "bu sikayet", "bu kayıt", "bu kayit", "seçili şikayet", "secili sikayet",
        "aktif şikayet", "mevcut şikayet", "bu başvurunun", "bu vakada", "bu arızada"
    ]
    if any(p in q_ascii or p in _normalize_tr(prompt) for p in context_phrases):
        cid = st.session_state.get("detail_id") or st.session_state.get("draft_selected_cid")
        if cid:
            item = get_complaint(int(cid))
            if item:
                return item, True
        return get_complaint(int(df.iloc[0]["id"])), True

    return None, False


def _resolve_target_complaint(prompt: str = ""):
    df = complaints()
    if df.empty:
        return None

    explicit_item, _ = _extract_explicit_complaint_from_prompt(prompt, df)
    if explicit_item:
        return explicit_item

    cid = st.session_state.get("detail_id") or st.session_state.get("draft_selected_cid")
    if cid:
        item = get_complaint(int(cid))
        if item:
            return item

    return get_complaint(int(df.iloc[0]["id"]))


def _execute_safe_readonly_sql(sql_query: str) -> str:
    """LLM tarafından üretilen SELECT sorgusunu güvenli (salt-okunur) modda çalıştırıp sonucu metin olarak döndürür."""
    if not sql_query:
        return ""
    clean_sql = sql_query.strip().rstrip(";")
    if not clean_sql.upper().startswith("SELECT"):
        return ""
    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "PRAGMA", "ATTACH", "REPLACE"]
    if any(re.search(rf'\b{kw}\b', clean_sql, re.I) for kw in forbidden):
        return ""
    try:
        with db() as conn:
            rows = conn.execute(f"{clean_sql} LIMIT 25").fetchall()
            if not rows:
                return "SQL Sorgusu Sonucu: Eşleşen satır bulunamadı (0 kayıt)."
            dict_rows = [dict(r) for r in rows]
            return f"SQL Sorgusu ({clean_sql}) Sonucu ({len(dict_rows)} satır):\n" + json.dumps(dict_rows, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"SQL Çalıştırma Notu: {exc}"


def _extract_nlp_filters(prompt: str, df: pd.DataFrame) -> dict:
    """Sorudaki varlıkları ve sohbet geçmişindeki bağlamı tespit ederek veriyi filtreler."""
    q = _normalize_tr(prompt)
    q_ascii = _ascii_fold(prompt)
    filtered = df.copy()
    filtered["dt"] = pd.to_datetime(filtered["created_at"], format="mixed", errors="coerce")
    filtered["cause_class"] = filtered["description"].apply(_extract_cause)

    active_filters = []
    current_memory = {}

    now = datetime.now()
    tr_months = {
        "ocak": (1, "Ocak"), "şubat": (2, "Şubat"), "subat": (2, "Şubat"),
        "mart": (3, "Mart"), "nisan": (4, "Nisan"), "mayıs": (5, "Mayıs"), "mayis": (5, "Mayıs"),
        "haziran": (6, "Haziran"), "temmuz": (7, "Temmuz"), "ağustos": (8, "Ağustos"), "agustos": (8, "Ağustos"),
        "eylül": (9, "Eylül"), "eylul": (9, "Eylül"), "ekim": (10, "Ekim"),
        "kasım": (11, "Kasım"), "kasim": (11, "Kasım"), "aralık": (12, "Aralık"), "aralik": (12, "Aralık")
    }

    matched_month = None
    for m_key, (m_num, m_label) in tr_months.items():
        if re.search(rf'\b{m_key}\b', q) or re.search(rf'\b{m_key}\b', q_ascii):
            matched_month = (m_num, m_label)
            break

    m_last_days = re.search(r'son\s+(\d+)\s*(gün|gun|hafta|ay)', q_ascii)
    m_year = re.search(r'\b(202\d)\b', q)

    if matched_month:
        m_num, m_label = matched_month
        if m_year:
            yr = int(m_year.group(1))
            filtered = filtered[(filtered["dt"].dt.month == m_num) & (filtered["dt"].dt.year == yr)]
            active_filters.append(f"Tarih: {m_label} {yr}")
        else:
            filtered = filtered[filtered["dt"].dt.month == m_num]
            active_filters.append(f"Ay: {m_label}")
    elif m_last_days and "trend" not in q_ascii:
        n_val = int(m_last_days.group(1))
        unit = m_last_days.group(2)
        days_cnt = n_val if "gun" in unit or "gün" in unit else (n_val * 7 if "hafta" in unit else n_val * 30)
        max_dt = filtered["dt"].max()
        ref_dt = max_dt if pd.notna(max_dt) else now
        filtered = filtered[filtered["dt"] >= (ref_dt - timedelta(days=days_cnt))]
        active_filters.append(f"Dönem: Son {n_val} {unit}")
    elif "bugün" in q or "bugun" in q_ascii:
        filtered = filtered[filtered["dt"].dt.date == now.date()]
        active_filters.append("Tarih: Bugün")
    elif "bu hafta" in q_ascii:
        filtered = filtered[filtered["dt"] >= (now - timedelta(days=7))]
        active_filters.append("Dönem: Bu Hafta")
    elif "geçen ay" in q or "gecen ay" in q_ascii or "önceki ay" in q:
        first_this = now.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        filtered = filtered[(filtered["dt"].dt.year == last_prev.year) & (filtered["dt"].dt.month == last_prev.month)]
        active_filters.append(f"Dönem: Geçen Ay ({last_prev.strftime('%Y-%m')})")
    elif "bu ay" in q_ascii:
        month_match = filtered[(filtered["dt"].dt.year == now.year) & (filtered["dt"].dt.month == now.month)]
        if not month_match.empty:
            filtered = month_match
            active_filters.append(f"Dönem: Bu Ay ({now.strftime('%Y-%m')})")
        else:
            active_filters.append(f"Dönem: Tüm Zamanlar (Bu ay [{now.strftime('%Y-%m')}] yeni kayıt bulunmadığı için)")
    elif m_year:
        yr = int(m_year.group(1))
        filtered = filtered[filtered["dt"].dt.year == yr]
        active_filters.append(f"Yıl: {yr}")

    status_ask_comparison = _fuzzy_has(q, ["durum dağılımı", "durumlarına göre", "kaçı açık kaçı kapalı", "statü dağılımı"])
    if not status_ask_comparison:
        if _fuzzy_has(q, ["incelemede", "incelenen"]):
            filtered = filtered[filtered["status"] == "İncelemede"]
            active_filters.append("Durum: İncelemede")
        elif _fuzzy_has(q, ["aksiyon bekliyor", "aksiyon bekleyen"]):
            filtered = filtered[filtered["status"] == "Aksiyon Bekliyor"]
            active_filters.append("Durum: Aksiyon Bekliyor")
        elif "doğrulama" in q and "doğrulan" not in q:
            filtered = filtered[filtered["status"] == "Doğrulama"]
            active_filters.append("Durum: Doğrulama")
        elif _fuzzy_has(q, ["yeni açılan", "yeni durumda", "durumu yeni"]):
            filtered = filtered[filtered["status"] == "Yeni"]
            active_filters.append("Durum: Yeni")
        elif _fuzzy_has(q, ["kapalı", "kapanmış", "kapatılan", "çözülmüş", "çözülen", "tamamlanan"]):
            filtered = filtered[filtered["status"] == "Kapalı"]
            active_filters.append("Durum: Kapalı")
        elif _fuzzy_has(q, ["açık şikayet", "açık kayıt", "açık olan", "kapanmamış", "aktif şikayet", "bekleyen şikayet", "açık döf", "acik sikayet", "acik kayit", "acik olan"]):
            filtered = filtered[filtered["status"] != "Kapalı"]
            active_filters.append("Durum: Açık (Kapalı Olmayan)")

    prio_ask_comparison = _fuzzy_has(q, ["öncelik dağılımı", "önceliklerine göre", "önem derecesine göre"])
    if not prio_ask_comparison:
        matched_prios = []
        if _fuzzy_has(q, ["kritik"]):
            matched_prios.append("Kritik")
        if re.search(r'\byuksek\b', q_ascii):
            matched_prios.append("Yüksek")
        if re.search(r'\borta\b', q_ascii) and "ortalama" not in q_ascii:
            matched_prios.append("Orta")
        if re.search(r'\bdusuk\b', q_ascii):
            matched_prios.append("Düşük")
        if matched_prios:
            filtered = filtered[filtered["priority"].isin(matched_prios)]
            active_filters.append(f"Öncelik: {', '.join(matched_prios)}")

    if _fuzzy_has(q, ["acil dönüş", "acil geri dönüş", "acil olan", "acil şikayet", "acil donus"]):
        filtered = filtered[filtered["urgent_return"].astype(int) == 1]
        active_filters.append("Acil Dönüş: Evet")

    validity_ask_comparison = _fuzzy_has(q, ["geçerlilik dağılımı", "haklı haksız oranı", "doğruluk oranı"])
    if not validity_ask_comparison:
        if _fuzzy_has(q, ["yersiz", "yanlış şikayet", "asılsız", "haksız"]):
            filtered = filtered[filtered["validity"] == "Yersiz / yanlış şikâyet"]
            active_filters.append("Geçerlilik: Yersiz / Yanlış Şikâyet")
        elif _fuzzy_has(q, ["geçerli şikayet", "haklı şikayet", "doğrulanmış şikayet"]):
            filtered = filtered[filtered["validity"] == "Geçerli şikâyet"]
            active_filters.append("Geçerlilik: Geçerli Şikâyet")

    cause_keywords_map = [
        (["aşırı ısınma", "ısınma", "sıcaklık", "termal", "overheat", "ısınıyor", "isinma", "sicaklik"], "Aşırı Isınma / Termal Sapma"),
        (["rulman", "aşınma", "bilya", "bearing", "worn", "asinma"], "Rulman / Mekanik Aşınma"),
        (["sıkışma", "kilitlen", "pens", "collet", "uç çıkmıyor", "uç takıl", "sikisma"], "Mekanik Sıkışma / Pens Kilitlenmesi"),
        (["kablo", "temassız", "konnektör", "kopuk kablo", "temassiz"], "Kablo / Bağlantı Temassızlığı"),
        (["korozyon", "oksit", "paslanma", "pas "], "Korozyon / Oksitlenme"),
        (["elektrik kaçağı", "kaçak akım", "hipot", "izolasyon", "çarpması", "kıvılcım", "kacak"], "Elektriksel Kaçak / İzolasyon"),
        (["ani durma", "çalışmıyor", "çalışmadı", "durdu", "fonksiyon kaybı", "calismiyor"], "Ani Durma / Fonksiyon Kaybı"),
        (["anormal ses", "titreşim", "gürültü", "ses yapıyor", "vibrasyon", "salgı", "titresim"], "Anormal Ses / Titreşim"),
        (["kırılma", "kırık", "çatlak", "koptu", "kopma", "parça kırıl", "kirik", "catlak", "kirilma"], "Fiziksel Kırılma / Çatlak"),
        (["ölçü", "tolerans", "çap", "boyut", "olcu"], "Ölçü / Tolerans Uygunsuzluğu"),
        (["teslimat", "sevkiyat", "paketleme", "ambalaj", "geç teslim"], "Sevkiyat / Paketleme Problemi"),
    ]
    for kw_list, cause_label in cause_keywords_map:
        if _fuzzy_has(q, kw_list, threshold=0.84):
            filtered = filtered[filtered["cause_class"] == cause_label]
            active_filters.append(f"Arıza Tipi: {cause_label}")
            current_memory["cause_class"] = cause_label
            break

    for cat in ["Ürün Kalitesi", "Ölçü / Tolerans", "Yüzey Hatası", "Teslimat", "Paketleme", "Hizmet"]:
        if _ascii_fold(cat) in q_ascii and "Arıza Tipi:" not in " ".join(active_filters):
            filtered = filtered[filtered["category"] == cat]
            active_filters.append(f"Kategori: {cat}")
            break

    matched_product = None
    unique_products = [str(p).strip() for p in df["product"].dropna().unique() if str(p).strip()]
    for prod_name in sorted(unique_products, key=len, reverse=True):
        p_ascii = _ascii_fold(prod_name)
        if len(p_ascii) >= 3 and p_ascii in q_ascii:
            matched_product = prod_name
            filtered = filtered[filtered["product"] == prod_name]
            active_filters.append(f"Ürün: {prod_name}")
            current_memory["product"] = prod_name
            break

    if not matched_product:
        generic_prod_words = {"urun", "urunu", "parca", "parcasi", "cihaz", "cihazi", "model", "modeli", "sistem", "medikal", "seri"}
        token_to_prods = {}
        for prod_name in unique_products:
            tokens = re.findall(r'[A-Za-z0-9ÇĞİÖŞÜçğıöşü-]{3,}', prod_name)
            for tk in tokens:
                tk_ascii = _ascii_fold(tk)
                if tk_ascii not in generic_prod_words and len(tk_ascii) >= 3:
                    token_to_prods.setdefault(tk_ascii, set()).add(prod_name)
        for tk_ascii in sorted(token_to_prods.keys(), key=len, reverse=True):
            if re.search(rf'\b{re.escape(tk_ascii)}', q_ascii):
                matched_prods_set = token_to_prods[tk_ascii]
                filtered = filtered[filtered["product"].isin(matched_prods_set)]
                label_str = list(matched_prods_set)[0] if len(matched_prods_set) == 1 else f"'{tk_ascii.upper()}' içeren ürünler"
                active_filters.append(f"Ürün/Model: {label_str}")
                current_memory["product_set"] = list(matched_prods_set)
                matched_product = label_str
                break

    unique_customers = [str(c).strip() for c in df["customer"].dropna().unique() if str(c).strip()]
    generic_cust_words = {"musteri", "firma", "sirket", "hastane", "hastanesi", "klinik", "medikal", "sanayi", "ltd", "sti", "anonim"}
    for cust_name in sorted(unique_customers, key=len, reverse=True):
        c_ascii = _ascii_fold(cust_name)
        if len(c_ascii) >= 3 and c_ascii in q_ascii:
            filtered = filtered[filtered["customer"] == cust_name]
            active_filters.append(f"Müşteri: {cust_name}")
            current_memory["customer"] = cust_name
            break
        else:
            c_tokens = [_ascii_fold(t) for t in re.findall(r'[A-Za-z0-9ÇĞİÖŞÜçğıöşü]{4,}', cust_name)]
            c_tokens = [t for t in c_tokens if t not in generic_cust_words]
            if c_tokens and any(re.search(rf'\b{re.escape(ct)}', q_ascii) for ct in c_tokens):
                filtered = filtered[filtered["customer"] == cust_name]
                active_filters.append(f"Müşteri: {cust_name}")
                current_memory["customer"] = cust_name
                break

    unique_owners = [str(o).strip() for o in df["owner"].dropna().unique() if str(o).strip() and str(o).strip() != "-"]
    for own_name in sorted(unique_owners, key=len, reverse=True):
        o_ascii = _ascii_fold(own_name)
        if len(o_ascii) >= 3 and o_ascii in q_ascii and "kalite360" not in q_ascii:
            filtered = filtered[filtered["owner"] == own_name]
            active_filters.append(f"Sorumlu: {own_name}")
            current_memory["owner"] = own_name
            break

    followup_pronouns = ["bunların", "bunlarin", "onların", "onlarin", "bu ürünün", "bu urunun", "bu müşterinin", "bu musterinin", "peki bunlarda", "peki onda", "aynı ürün", "bu arıza"]
    last_mem = st.session_state.get("ai_last_entities") or {}
    if any(fp in q_ascii or fp in q for fp in followup_pronouns) and last_mem and not current_memory:
        if last_mem.get("product"):
            filtered = filtered[filtered["product"] == last_mem["product"]]
            active_filters.append(f"Önceki Bağlam (Ürün): {last_mem['product']}")
        elif last_mem.get("product_set"):
            filtered = filtered[filtered["product"].isin(last_mem["product_set"])]
            active_filters.append("Önceki Bağlam (Model Grubu)")
        if last_mem.get("customer"):
            filtered = filtered[filtered["customer"] == last_mem["customer"]]
            active_filters.append(f"Önceki Bağlam (Müşteri): {last_mem['customer']}")
        if last_mem.get("cause_class"):
            filtered = filtered[filtered["cause_class"] == last_mem["cause_class"]]
            active_filters.append(f"Önceki Bağlam (Arıza): {last_mem['cause_class']}")

    if current_memory:
        st.session_state["ai_last_entities"] = current_memory

    return {
        "filtered_df": filtered,
        "active_filters": active_filters,
    }


def _build_comprehensive_knowledge_pack(df: pd.DataFrame, fdf: pd.DataFrame, active_filters: list, active_item: dict = None, extra_sql_result: str = "") -> str:
    """
    Yapay zekânın sorulan her soruya %100 doğru rakamlarla ve esnek şekilde cevap verebilmesi için
    veritabanının tam analitik özetini (JSON/Markdown paketini) hazırlar.
    """
    total_all = len(df)
    open_all = len(df[df["status"] != "Kapalı"])
    closed_all = total_all - open_all

    f_total = len(fdf)
    f_open = len(fdf[fdf["status"] != "Kapalı"])
    f_closed = f_total - f_open

    prod_breakdown = "\n".join([f"  - {k}: {v} şikâyet (%{(v/f_total*100):.1f})" for k, v in fdf["product"].value_counts().head(10).items()]) if not fdf.empty else "  - Kayıt yok"
    cust_breakdown = "\n".join([f"  - {k}: {v} şikâyet (%{(v/f_total*100):.1f})" for k, v in fdf["customer"].value_counts().head(10).items()]) if not fdf.empty else "  - Kayıt yok"
    cause_breakdown = "\n".join([f"  - {k}: {v} vaka (%{(v/f_total*100):.1f})" for k, v in fdf["cause_class"].value_counts().head(10).items()]) if not fdf.empty else "  - Kayıt yok"
    status_breakdown = ", ".join([f"{k}: {v}" for k, v in fdf["status"].value_counts().items()]) if not fdf.empty else "Yok"
    prio_breakdown = ", ".join([f"{k}: {v}" for k, v in fdf["priority"].value_counts().items()]) if not fdf.empty else "Yok"
    validity_breakdown = ", ".join([f"{k}: {v}" for k, v in fdf["validity"].fillna("Değerlendirilmedi").value_counts().items()]) if not fdf.empty else "Yok"
    owner_breakdown = ", ".join([f"{k}: {v}" for k, v in fdf["owner"].fillna("Atanmamış").value_counts().head(8).items()]) if not fdf.empty else "Yok"

    # Aylık dağılım
    monthly_str = "Yok"
    if not fdf.empty and "dt" in fdf.columns:
        m_counts = fdf["dt"].dt.strftime("%Y-%m").value_counts().sort_index(ascending=False).head(6)
        monthly_str = ", ".join([f"{k}: {v} adet" for k, v in m_counts.items()])

    # Son 30 gün trendi
    max_dt = fdf["dt"].max() if not fdf.empty else pd.NaT
    ref_date = max_dt if pd.notna(max_dt) else datetime.now()
    last_30 = len(fdf[fdf["dt"] >= (ref_date - timedelta(days=30))]) if not fdf.empty else 0
    prev_30 = len(fdf[(fdf["dt"] < (ref_date - timedelta(days=30))) & (fdf["dt"] >= (ref_date - timedelta(days=60)))]) if not fdf.empty else 0

    # Açık DÖF / Aksiyonlar
    with db() as conn:
        act_rows = conn.execute(
            "SELECT a.title, a.assignee, a.due_date, a.status, c.complaint_no, c.product "
            "FROM actions a LEFT JOIN complaints c ON a.complaint_id = c.id "
            "WHERE a.status != 'Kapalı' ORDER BY a.due_date ASC LIMIT 10"
        ).fetchall()
    dof_lines = "\n".join([
        f"  - [{r['complaint_no'] or '-'}] {r['product'] or '-'}: {r['title']} (Sorumlu: {r['assignee']}, Termin: {r['due_date']}, Durum: {r['status']})"
        for r in act_rows
    ]) if act_rows else "  - Sistemde açık DÖF/aksiyon kaydı bulunmuyor."

    # Eşleşen örnek şikâyet kayıtları
    sample_records = []
    for _, r in fdf.head(8).iterrows():
        sample_records.append(
            f"  - {r.get('complaint_no')} ({str(r.get('created_at') or '')[:10]}): Müşteri={r.get('customer')}, "
            f"Ürün={r.get('product')}, Arıza={r.get('cause_class')}, Öncelik={r.get('priority')}, "
            f"Durum={r.get('status')}, Sorumlu={r.get('owner')}, KökNeden={str(r.get('root_cause') or 'Girilmemiş')[:60]}"
        )
    samples_md = "\n".join(sample_records) if sample_records else "  - Eşleşen kayıt yok."

    active_comp_md = ""
    if active_item:
        ctx_a = inspect_complaint_context(active_item)
        active_comp_md = (
            f"\n=== SEÇİLİ / ODAK ŞİKÂYET KAYDI ({ctx_a['complaint_no']}) ===\n"
            f"- Müşteri: {ctx_a['customer']} (İlgili: {ctx_a['contact'] or '-'}, E-posta: {ctx_a['email']}, Tel: {ctx_a['phone']})\n"
            f"- Ürün: {ctx_a['product']} | Seri/Lot/Sipariş No: {ctx_a['order_no'] or 'Belirtilmemiş'}\n"
            f"- Kategori & Arıza Tipi: {ctx_a['category']} / {ctx_a['issue_topic']}\n"
            f"- Durum: {ctx_a['status']} | Öncelik: {ctx_a['priority']} | Geçerlilik: {ctx_a['validity']} | Sorumlu: {active_item.get('owner') or '-'}\n"
            f"- Tarih: {ctx_a['created_date']} ({ctx_a['elapsed_days']} gündür sistemde)\n"
            f"- Şikâyet Metni: {ctx_a['description'][:350]}\n"
            f"- Müşteri Talebi: {ctx_a['requested'] or 'Belirtilmemiş'}\n"
            f"- Teknik Bulgular: {ctx_a['findings'] or 'Girilmemiş'}\n"
            f"- Doğrulanmış Kök Neden: {ctx_a['root_cause'] or 'Henüz doğrulanmış kök neden girilmemiş'}\n"
            f"- Çözüm / Aksiyon Planı: {ctx_a['action_plan'] or 'Henüz çözüm planı girilmemiş'}"
        )

    sql_section = f"\n=== ÖZEL SQL SORGUSU SONUCU ===\n{extra_sql_result}\n" if extra_sql_result else ""

    return f"""=== KALİTE360 VERİTABANI CANLI VERİ PAKETİ ===
- Tüm Veritabanı Toplamı: {total_all} şikâyet ({open_all} Açık, {closed_all} Kapalı)
- Kullanıcının Sorusuna Göre Uygulanan Filtreler: {', '.join(active_filters) if active_filters else 'Filtre yok (Tüm Veritabanı)'}
- Filtreye Uyan Kayıt Sayısı: {f_total} şikâyet ({f_open} Açık, {f_closed} Kapalı)
- Son 30 Gün Vaka Sayısı: {last_30} adet (Önceki 30 gün: {prev_30} adet)
- Aylık Dağılım (Son Aylar): {monthly_str}
- Durum Dağılımı: {status_breakdown}
- Öncelik Dağılımı: {prio_breakdown}
- Geçerlilik Dağılımı: {validity_breakdown}
- Sorumlu Personel Dağılımı: {owner_breakdown}

[ÜRÜN / MODEL DAĞILIMI (İlk 10)]:
{prod_breakdown}

[MÜŞTERİ / KURUM DAĞILIMI (İlk 10)]:
{cust_breakdown}

[HATA / ARIZA NEDENİ DAĞILIMI (Pareto)]:
{cause_breakdown}

[AÇIK DÖF / AKSİYON GÖREVLERİ]:
{dof_lines}

[FİLTREYE UYAN ÖRNEK ŞİKÂYET KAYITLARI]:
{samples_md}
{active_comp_md}
{sql_section}"""


def _try_llm_sql_generation(user_prompt: str) -> str:
    """
    Kullanıcı karmaşık/çapraz bir veri sorusu sorduğunda LLM'e güvenli bir SQLite SELECT
    sorgusu yazdırıp sonucu gerçek veritabanından çeker.
    """
    schema_prompt = f"""Kullanıcının sorusunu yanıtlamak için SQLite `complaints` tablosuna bir SELECT sorgusu gerekip gerekmediğine karar ver.
Tablo Şeması:
complaints(id, complaint_no, customer, contact, phone, email, product, order_no, category, priority, description, requested, owner, status, findings, root_cause, action_type, action_plan, validity, urgent_return, sentiment_score, priority_rank, created_at)
actions(id, complaint_id, title, assignee, due_date, status, created_at)

Kullanıcı Sorusu: "{user_prompt}"

Sadece JSON formatında yanıt ver:
{{"needs_custom_sql": true veya false, "sql": "SELECT ... FROM complaints ... LIMIT 15"}}"""
    raw = query_ollama_chat(
        messages=[{"role": "user", "content": schema_prompt}],
        temperature=0.0,
        timeout=8,
        json_mode=True
    )
    if not raw:
        return ""
    try:
        m = re.search(r'\{.*\}', raw, re.S)
        parsed = json.loads(m.group(0) if m else raw)
        if parsed.get("needs_custom_sql") and parsed.get("sql"):
            return _execute_safe_readonly_sql(str(parsed["sql"]))
    except Exception:
        pass
    return ""


def _generate_agentic_ai_response(user_prompt: str, knowledge_pack: str) -> str | None:
    """
    Sabit şablon basmak yerine, doğrudan LLM'in gerçek veritabanı paketiyle
    kullanıcının sorusuna özel, doğal, akıllı ve nokta atışı yanıt üretmesini sağlar.
    """
    if not st.session_state.get("use_ollama_ai", True):
        return None

    status = get_ollama_status()
    if not status["available"]:
        return None

    system_prompt = """Sen Kalite360 Fabrika & Medikal Sistemler A.Ş.'nin Yapay Zekâ Kalite Güvence Başmühendisisin.
Kullanıcı seninle günlük konuşma diliyle, samimi, bazen devrik cümlelerle veya yazım hatalarıyla konuşabilir.

KESİN ÇALIŞMA KURALLARIN:
1. ELMA SORULUYORSA SADECE ELMAYI ANLAT: Kullanıcının sorusundaki asıl niyeti tam olarak anla. Ne sorduysa doğrudan, net ve eksiksiz olarak onu cevapla. Kullanıcının sormadığı alakasız istatistikleri veya ezbere şablonları dayatma.
2. %100 GERÇEK VERİ KULLAN: Yanıtındaki tüm sayıları, şikâyet numaralarını (`SK-...`), ürün ve müşteri isimlerini sana verilen `=== KALİTE360 VERİTABANI CANLI VERİ PAKETİ ===` içinden al. Veritabanında olmayan hiçbir sayıyı veya kaydı uydurma.
3. KALİTE MÜHENDİSİ ZEKÂSI: Kullanıcı yorum, tavsiye, kök neden (6M/Ishikawa), DÖF/CAPA, 8D, FMEA veya "nasıl çözeriz / ne yapmalıyız" gibi mühendislik görüşü isterse; ISO 9001, ISO 13485 ve IATF 16949 standartlarına hâkim deneyimli bir Kalite Mühendisi gibi somut, teknik ve uygulanabilir çözümler sun.
4. FORMAT: Yanıtını okunaklı Markdown (gerekiyorsa temiz bir tablo veya maddeler) halinde, doğal ve akıcı bir Türkçe ile yaz."""

    messages = [{"role": "system", "content": system_prompt}]

    # Sohbet hafızası: Önceki mesajları ekle ki takip sorularını ("peki bunların kaçı açık?", "sence neden?") tam anlasın
    recent_hist = (st.session_state.get("ai_chat_history") or [])[-6:-1]
    for h in recent_hist:
        r_role = h.get("role", "user")
        if r_role in ["user", "assistant"]:
            messages.append({"role": r_role, "content": str(h.get("content", ""))[:700]})

    user_message = (
        f"{knowledge_pack}\n\n"
        f"=== KULLANICININ ŞU ANKİ SORUSU ===\n"
        f"{user_prompt}\n\n"
        f"Lütfen yukarıdaki canlı veritabanı verilerine dayanarak kullanıcının sorusunu doğrudan, doğal ve tam istediği şekilde yanıtla:"
    )
    messages.append({"role": "user", "content": user_message})

    model_to_use = st.session_state.get("selected_ollama_model") or status["active_model"]
    return query_ollama_chat(messages, model=model_to_use, temperature=0.2, timeout=30)


def _fallback_deterministic_answer(raw_prompt: str, df: pd.DataFrame, fdf: pd.DataFrame, active_filters: list, explicit_item: dict = None) -> str:
    """Eğer bilgisayarda Ollama veya Bulut API kapalıysa devreye giren yerel NLP yedek yanıtlayıcısı."""
    q = _normalize_tr(raw_prompt)
    filter_badge = f"**Uygulanan Filtreler:** `{' | '.join(active_filters)}`\n\n" if active_filters else ""

    if explicit_item:
        ctx = inspect_complaint_context(explicit_item)
        return (
            f"📄 **`{ctx['complaint_no']}` Şikâyet Kaydı Bilgileri:**\n\n"
            f"- **Müşteri / Kurum:** {ctx['customer']} ({ctx['contact'] or '-'})\n"
            f"- **Ürün & Seri/Lot No:** {ctx['product']} · `{ctx['order_no'] or 'Belirtilmemiş'}`\n"
            f"- **Kategori & Arıza Tipi:** {ctx['category']} / `{ctx['issue_topic']}`\n"
            f"- **Durum & Öncelik:** `{ctx['status']}` · `{ctx['priority']}` (Sorumlu: `{explicit_item.get('owner') or '-'}`)\n"
            f"- **Şikâyet Açıklaması:** {ctx['description'] or '-'}\n"
            f"- **Kök Neden:** {ctx['root_cause'] or 'Henüz girilmemiş'}\n"
            f"- **Çözüm Planı:** {ctx['action_plan'] or 'Henüz girilmemiş'}"
        )

    if fdf.empty:
        return (
            f"🔍 **Eşleşen Kayıt Bulunamadı:**\n\n{filter_badge}"
            f"Belirttiğiniz kriterlere uygun şikâyet kaydı veritabanında mevcut değil."
        )

    total_f = len(fdf)
    open_f = len(fdf[fdf["status"] != "Kapalı"])

    if _fuzzy_has(q, ["hata neden", "arıza neden", "en sık", "pareto", "hangi hata", "hangi arıza"]):
        causes = fdf["cause_class"].value_counts()
        rows_md = "\n".join([f"| {c} | {cnt} | %{(cnt / total_f * 100):.1f} |" for c, cnt in causes.head(8).items()])
        return f"🔍 **Hata Nedenleri Analizi ({total_f} Kayıt):**\n\n{filter_badge}| Hata Nedeni | Vaka Sayısı | Oran |\n|---|---|---|\n{rows_md}"

    if _fuzzy_has(q, ["hangi müşteri", "hangi firma", "müşteri dağılımı"]):
        custs = fdf["customer"].value_counts()
        rows_md = "\n".join([f"| {c} | {cnt} | %{(cnt / total_f * 100):.1f} |" for c, cnt in custs.head(8).items()])
        return f"🏢 **Müşteri Bazlı Şikâyet Dağılımı ({total_f} Kayıt):**\n\n{filter_badge}| Müşteri | Şikâyet Sayısı | Oran |\n|---|---|---|\n{rows_md}"

    if _fuzzy_has(q, ["hangi ürün", "hangi model", "ürün dağılımı", "en çok şikayet"]):
        prods = fdf["product"].value_counts()
        rows_md = "\n".join([f"| {p} | {cnt} | %{(cnt / total_f * 100):.1f} |" for p, cnt in prods.head(8).items()])
        return f"📊 **Ürün Bazlı Şikâyet Dağılımı ({total_f} Kayıt):**\n\n{filter_badge}| Ürün / Model | Şikâyet Sayısı | Oran |\n|---|---|---|\n{rows_md}"

    rows_md = "\n".join([
        f"| `{r['complaint_no']}` | {str(r['customer'])[:18]} | {str(r['product'])[:20]} | {r['cause_class'][:22]} | {r['priority']} | {r['status']} |"
        for _, r in fdf.head(8).iterrows()
    ])
    return (
        f"📋 **Sorgu Sonucu: Toplam `{total_f}` Kayıt (`{open_f}` Açık):**\n\n"
        f"{filter_badge}"
        f"| Şikâyet No | Müşteri | Ürün | Arıza Tipi | Öncelik | Durum |\n|---|---|---|---|---|---|\n{rows_md}"
    )


def process_ai_query(prompt: str, user: dict) -> str:
    raw_prompt = (prompt or "").strip()
    if not raw_prompt:
        return "Lütfen sormak istediğiniz soruyu yazın."

    df = complaints()
    if df.empty:
        return "⚠️ Veritabanında henüz kayıtlı şikâyet bulunmuyor. **Yeni Şikâyet** veya **Veri Aktarımı** sayfasından veri ekleyebilirsiniz."

    # 1. Müşteri Geri Dönüş / Yanıt Taslağı İstemi Kontrolü
    intent_draft = _detect_response_intent_from_chat(raw_prompt)
    if intent_draft:
        ok, auth_msg = is_user_authorized(user)
        if not ok:
            return f"🔒 **Yetkisiz Erişim:** {auth_msg}"

        target_item = _resolve_target_complaint(raw_prompt)
        if not target_item:
            return "⚠️ Üzerinde çalışılacak bir şikâyet kaydı bulunamadı. Lütfen önce **Şikâyet Listesi**'nden bir kayıt seçin."

        use_llm_flag = bool(st.session_state.get("use_ollama_ai", True) and get_ollama_status()["available"])
        res = generate_customer_response_draft(
            item=target_item,
            purpose=intent_draft["purpose"],
            channel=intent_draft["channel"],
            tone=intent_draft["tone"],
            length_opt="Standart",
            extra_instruction=raw_prompt,
            use_llm=use_llm_flag
        )
        cid = int(target_item["id"])
        st.session_state["draft_selected_cid"] = cid
        st.session_state["draft_purpose"] = res["resolved_purpose"]
        st.session_state["draft_channel"] = res["channel"]
        st.session_state["draft_tone"] = res["tone"]
        st.session_state["draft_length"] = res["length_opt"]
        st.session_state[f"draft_editor_text_{cid}"] = res["draft_text"]
        st.session_state[f"draft_last_meta_{cid}"] = res

        ctx = res["context"]
        warn_block = "\n".join(res["guardrail_warnings"]) + "\n\n" if res["guardrail_warnings"] else ""
        missing_block = ""
        if ctx["missing_fields"]:
            q_line = f"❓ *{ctx['clarifying_questions'][0]}*\n\n" if ctx["clarifying_questions"] else "\n"
            missing_block = f"ℹ️ **Veritabanında Eksik Olan Alanlar (Uydurulmadı):** `{', '.join(ctx['missing_fields'])}`\n{q_line}"

        return (
            f"✉️ **`{ctx['complaint_no']}` ({ctx['product']}) İçin Müşteri Geri Dönüş Taslağı Hazırlandı**\n"
            f"**Amaç:** `{res['resolved_purpose']}` | **Üslup:** `{res['tone']}` | **Kanal:** `{res['channel']}`\n\n"
            f"{warn_block}{missing_block}"
            f"```text\n{res['draft_text']}\n```\n\n"
            f"💡 *Bu metni düzenlemek, kısaltmak, daha empatik/teknik yapmak veya şikâyet kaydına taslak olarak kaydetmek için üstteki **✉️ Müşteri Geri Dönüşü Oluştur** sekmesine geçebilirsiniz.*"
        )

    # 2. Açıkça Tam 6M Şablonu veya 8D/DÖF Formu Oluşturma Komutuysa
    target_for_qe = _resolve_target_complaint(raw_prompt)
    qe_response = detect_and_handle_quality_query(raw_prompt, target_for_qe, user)
    if qe_response:
        return qe_response

    # 3. Veritabanı Varlık Filtrelemesi + Canlı Veri Paketi Hazırlığı
    explicit_item, is_explicit_single = _extract_explicit_complaint_from_prompt(raw_prompt, df)
    nlp_res = _extract_nlp_filters(raw_prompt, df)
    fdf = nlp_res["filtered_df"]
    active_filters = nlp_res["active_filters"]

    # Karmaşık sorular için dinamik salt-okunur SQL sorgusu denemesi
    extra_sql_res = ""
    if st.session_state.get("use_ollama_ai", True) and get_ollama_status()["available"]:
        extra_sql_res = _try_llm_sql_generation(raw_prompt)

    focus_item = explicit_item if is_explicit_single else target_for_qe
    knowledge_pack = _build_comprehensive_knowledge_pack(
        df=df,
        fdf=fdf,
        active_filters=active_filters,
        active_item=focus_item,
        extra_sql_result=extra_sql_res
    )

    # 4. AGENTIC AI DOĞRUDAN YANIT ÜRETİMİ (Sabit şablon olmadan, doğrudan soruya özel yanıt)
    ai_direct_answer = _generate_agentic_ai_response(raw_prompt, knowledge_pack)
    if ai_direct_answer:
        return ai_direct_answer

    # 5. YEDEK (Eğer Ollama/API kapalıysa yerleşik deterministik NLP yanıtı)
    return _fallback_deterministic_answer(
        raw_prompt=raw_prompt,
        df=df,
        fdf=fdf,
        active_filters=active_filters,
        explicit_item=explicit_item if is_explicit_single else None
    )


# --- 7. TEK TIKLA KOPYALAMA YARDIMCISI ---
def render_copy_button(text_to_copy: str, key_suffix: str):
    escaped = json.dumps(text_to_copy)
    components.html(
        f"""
        <div style="margin:0; padding:0;">
            <button id="copy_btn_{key_suffix}" onclick='navigator.clipboard.writeText({escaped}).then(() => {{
                const b = document.getElementById("copy_btn_{key_suffix}");
                b.innerText = "✅ Panoya Kopyalandı!";
                b.style.backgroundColor = "#dcfce7";
                b.style.borderColor = "#16a34a";
                b.style.color = "#15803d";
                setTimeout(() => {{
                    b.innerText = "📋 Metni Panoya Kopyala";
                    b.style.backgroundColor = "#f8fafc";
                    b.style.borderColor = "#cbd5e1";
                    b.style.color = "#1e293b";
                }}, 2000);
            }})' style="width:100%; height:34px; border-radius:8px; border:1px solid #cbd5e1; background:#f8fafc; color:#1e293b; font-family:sans-serif; font-size:12.5px; font-weight:600; cursor:pointer;">
                📋 Metni Panoya Kopyala
            </button>
        </div>
        """,
        height=40,
    )


# --- 8. ANA WIDGET RENDER FONKSİYONU ---
def render_chat():
    if "ai_chat_fullscreen" not in st.session_state:
        st.session_state.ai_chat_fullscreen = False
    if "use_ollama_ai" not in st.session_state:
        st.session_state.use_ollama_ai = True

    is_fs = st.session_state.ai_chat_fullscreen
    inject_custom_css(is_fs)
    init_assistant_tables()

    user = st.session_state.get("user") or {}
    username = user.get("username", "misafir")

    if "ai_chat_history" not in st.session_state or st.session_state.get("ai_chat_user") != username:
        st.session_state.ai_chat_history = load_user_chat_history(username)
        st.session_state.ai_chat_user = username

    ollama_info = get_ollama_status()

    with st.popover("🤖", help="Kalite360 Yapay Zekâ ve Müşteri İletişim Asistanı"):
        hdr_c1, hdr_c2 = st.columns([7.5, 1.3] if is_fs else [4.3, 1.2])
        with hdr_c1:
            if ollama_info["available"] and st.session_state.use_ollama_ai:
                eng_lbl = ollama_info["active_engine"]
                mdl_lbl = st.session_state.get("selected_ollama_model") if eng_lbl == "Ollama" else ollama_info["active_model"]
                mdl_lbl = mdl_lbl or ollama_info["active_model"]
                st.markdown(f"##### 🤖 Kalite360 AI Asistanı <span style='font-size:11px; background:#dcfce7; color:#15803d; padding:2px 7px; border-radius:10px;'>🟢 {eng_lbl}: {mdl_lbl}</span>", unsafe_allow_html=True)
            else:
                st.markdown("##### 🤖 Kalite360 AI Asistanı <span style='font-size:11px; background:#fef3c7; color:#b45309; padding:2px 7px; border-radius:10px;'>⚡ Yerleşik NLP (AI Ayarlarından Model Seçin)</span>", unsafe_allow_html=True)
        with hdr_c2:
            fs_label = "🗗 Küçült" if is_fs else "⛶ Tam Ekran"
            fs_help = "Yarım ekran boyutuna geri dön (Küçült)" if is_fs else "Tam ekran yap (Büyüt)"
            if st.button(fs_label, key="btn_toggle_ai_fullscreen", help=fs_help, use_container_width=True):
                st.session_state.ai_chat_fullscreen = not is_fs
                st.rerun()

        tab_chat, tab_draft, tab_qe = st.tabs([
            "💬 Sohbet & Analiz",
            "✉️ Müşteri Geri Dönüşü Oluştur",
            "🧩 6M & DÖF / 8D"
        ])

        # SEKME 1: SOHBET & DOĞAL DİL KOMUTLARI
        with tab_chat:
            top_c1, top_c2, top_c3 = st.columns([2.7, 1.1, 0.9])
            top_c1.caption("Canlı SQL & RAG Destekli Kalite Mühendisi Yapay Zekâsı")
            st.session_state.use_ollama_ai = top_c2.toggle(
                "🧠 AI Aktif",
                value=st.session_state.use_ollama_ai,
                key="tgl_ollama_mode",
                help="Açık olduğunda sabit şablonlar yerine yapay zekâ veritabanını analiz ederek doğrudan yanıt verir."
            )
            if top_c3.button("🗑️ Temizle", key="btn_clear_ai_chat"):
                clear_user_chat_history(username)
                st.session_state.ai_chat_history = load_user_chat_history(username)
                st.session_state.pop("ai_last_entities", None)
                st.rerun()

            # YAPAY ZEKÂ BEYİN VE MODEL SEVİYESİ YÜKSELTME PANELİ
            with st.expander("🧠 Yapay Zekâ Beyin & Model Ayarları (Zekâ Seviyesini Yükselt)", expanded=not ollama_info["available"]):
                saved_cfg = ollama_info.get("config", {})
                prov_options = ["auto", "gemini", "openai_compat", "ollama"]
                prov_labels = {
                    "auto": "🔄 Otomatik (Bulut API varsa onu, yoksa Yerel Ollama'yı kullan)",
                    "gemini": "✨ Google Gemini API (Çok Yüksek Türkçe Zekâsı - Ücretsiz API Destekli)",
                    "openai_compat": "🚀 Groq / OpenAI / DeepSeek API (Llama-3.3-70B / GPT-4o Seviyesi)",
                    "ollama": "💻 Yerel Ollama (İnternetsiz Bilgisayar Üzerinde Çalışır)"
                }
                curr_prov = saved_cfg.get("ai_provider", "auto")
                if curr_prov not in prov_options:
                    curr_prov = "auto"

                sel_prov = st.selectbox(
                    "Aktif Yapay Zekâ Motoru:",
                    options=prov_options,
                    index=prov_options.index(curr_prov),
                    format_func=lambda x: prov_labels.get(x, x),
                    key="ui_ai_provider_sel"
                )

                if sel_prov in ["gemini", "auto"]:
                    st.markdown("**1. Google Gemini Ayarları (En Yüksek Anlama Kabiliyeti İçin Önerilen):**")
                    g_c1, g_c2 = st.columns([2, 1])
                    gem_key = g_c1.text_input(
                        "Gemini API Anahtarı (aistudio.google.com üzerinden ücretsiz alınır):",
                        value=saved_cfg.get("gemini_api_key", ""),
                        type="password",
                        key="ui_gem_key"
                    )
                    gem_mdl = g_c2.text_input(
                        "Gemini Modeli:",
                        value=saved_cfg.get("gemini_model", "gemini-2.5-flash"),
                        key="ui_gem_mdl"
                    )
                else:
                    gem_key = saved_cfg.get("gemini_api_key", "")
                    gem_mdl = saved_cfg.get("gemini_model", "gemini-2.5-flash")

                if sel_prov == "openai_compat":
                    st.markdown("**2. Groq / OpenAI / DeepSeek API Ayarları:**")
                    o_c1, o_c2, o_c3 = st.columns([1.5, 1.5, 1])
                    oa_key = o_c1.text_input("API Key:", value=saved_cfg.get("openai_api_key", ""), type="password", key="ui_oa_key")
                    oa_url = o_c2.text_input("Base URL:", value=saved_cfg.get("openai_base_url", "https://api.groq.com/openai/v1"), key="ui_oa_url")
                    oa_mdl = o_c3.text_input("Model:", value=saved_cfg.get("openai_model", "llama-3.3-70b-versatile"), key="ui_oa_mdl")
                else:
                    oa_key = saved_cfg.get("openai_api_key", "")
                    oa_url = saved_cfg.get("openai_base_url", "https://api.groq.com/openai/v1")
                    oa_mdl = saved_cfg.get("openai_model", "llama-3.3-70b-versatile")

                if ollama_info["ollama_available"]:
                    sel_ol_mdl = st.selectbox(
                        "Yerel Ollama Modeli:",
                        options=ollama_info["models"],
                        index=ollama_info["models"].index(ollama_info["active_model"]) if ollama_info["active_model"] in ollama_info["models"] else 0,
                        key="ui_ollama_mdl_sel"
                    )
                    st.session_state["selected_ollama_model"] = sel_ol_mdl
                    if "3b" in sel_ol_mdl.lower():
                        st.caption("💡 **İpucu:** Şu an 3B (küçük) bir yerel model kullanıyorsunuz. Terminalde `ollama pull qwen2.5:7b` veya `ollama pull gemma2:9b` çalıştırarak yerel anlama kalitesini 3 kat artırabilir ya da yukarıya ücretsiz bir **Gemini API Key** girebilirsiniz.")
                else:
                    sel_ol_mdl = saved_cfg.get("ollama_model", "qwen2.5:7b")
                    st.caption("ℹ️ Yerel Ollama şu an çalışmıyor. Terminalde `ollama serve` yazabilir veya yukarıya **Gemini API Anahtarı** girebilirsiniz.")

                if st.button("💾 Yapay Zekâ Motor Ayarlarını Kaydet", key="btn_save_ai_engine_cfg", use_container_width=True):
                    save_company_settings({
                        "ai_ai_provider": sel_prov,
                        "ai_gemini_api_key": gem_key.strip(),
                        "ai_gemini_model": gem_mdl.strip(),
                        "ai_openai_api_key": oa_key.strip(),
                        "ai_openai_base_url": oa_url.strip(),
                        "ai_openai_model": oa_mdl.strip(),
                        "ai_ollama_model": sel_ol_mdl,
                    })
                    st.success("✅ Yapay Zekâ motor ayarları kaydedildi!")
                    st.rerun()

            msg_container = st.container(height=560 if is_fs else 420, border=True)
            with msg_container:
                for msg in st.session_state.ai_chat_history:
                    with st.chat_message(msg["role"]):
                        st.markdown(msg["content"])

            st.markdown("<div style='font-size:11.5px; font-weight:600; color:#475569; margin:3px 0;'>⚡ Hızlı Analiz & Müşteri Yanıt Komutları:</div>", unsafe_allow_html=True)

            c_prompt = None
            r1c1, r1c2 = st.columns(2)
            if r1c1.button("📊 En Çok Şikâyet", key="qa_1"):
                c_prompt = "Bu ay en çok hangi ürün için şikâyet aldık?"
            if r1c2.button("⚠️ En Sık Hatalar", key="qa_2"):
                c_prompt = "En sık karşılaşılan hata nedenlerini analiz et."

            r2c1, r2c2 = st.columns(2)
            if r2c1.button("🧩 6M Kök Neden", key="qa_3"):
                c_prompt = "Bu şikâyet için 6M kök neden analizi hazırla."
            if r2c2.button("📋 Açık & Gecikmiş DÖF", key="qa_4"):
                c_prompt = "Açık ve gecikmiş DÖF kayıtlarını listele."

            r3c1, r3c2 = st.columns(2)
            if r3c1.button("📈 30 Günlük Trend", key="qa_5"):
                c_prompt = "Son 30 günlük şikâyet trendini değerlendir."
            if r3c2.button("✉️ Geri Dönüş Hazırla", key="qa_6"):
                c_prompt = "Bu şikâyete uygun bir müşteri geri dönüş metni hazırla."

            r4c1, r4c2 = st.columns(2)
            if r4c1.button("⏳ İnceleme Sürüyor Maili", key="qa_7"):
                c_prompt = "Müşteriye incelemenin devam ettiğini bildiren bir e-posta yaz."
            if r4c2.button("📸 Eksik Bilgi Talep Et", key="qa_8"):
                c_prompt = "Müşteriden eksik teknik bilgileri talep eden bir mesaj oluştur."

            with st.form("ai_chat_send_form", clear_on_submit=True):
                in_col, btn_col = st.columns([4.2, 1])
                user_input = in_col.text_input(
                    "Mesaj",
                    placeholder="Bana istediğin gibi sor: 'Hangi ürünlerde ısınma var ve sence nasıl çözeriz?'...",
                    label_visibility="collapsed"
                )
                send_clicked = btn_col.form_submit_button("➤", use_container_width=True)

            active_prompt = c_prompt if c_prompt else (user_input.strip() if send_clicked and user_input.strip() else None)

            if active_prompt:
                st.session_state.ai_chat_history.append({"role": "user", "content": active_prompt})
                save_chat_message(username, "user", active_prompt)

                with msg_container:
                    with st.chat_message("user"):
                        st.markdown(active_prompt)
                    with st.chat_message("assistant"):
                        spinner_txt = f"Kalite360 veritabanı ve {ollama_info['active_engine']} analiz ediyor..." if (ollama_info["available"] and st.session_state.use_ollama_ai) else "Kalite360 veritabanı değerlendiriliyor..."
                        with st.spinner(spinner_txt):
                            try:
                                reply = process_ai_query(active_prompt, user)
                            except Exception as exc:
                                reply = f"❌ İşlem sırasında bir hata oluştu: `{exc}`"
                            st.markdown(reply)

                st.session_state.ai_chat_history.append({"role": "assistant", "content": reply})
                save_chat_message(username, "assistant", reply)
                st.rerun()

        # SEKME 2: ŞİKÂYETE ÖZEL MÜŞTERİ GERİ DÖNÜŞ METNİ OLUŞTURUCU
        with tab_draft:
            ok_auth, auth_msg = is_user_authorized(user)
            if not ok_auth:
                st.error(f"🔒 {auth_msg}")
                return

            df_all = complaints()
            if df_all.empty:
                st.info("Sistemde henüz kayıtlı şikâyet bulunmuyor.")
                return

            active_detail_id = st.session_state.get("detail_id")
            if active_detail_id and st.session_state.get("_last_synced_detail_id") != active_detail_id:
                st.session_state["draft_selected_cid"] = int(active_detail_id)
                st.session_state["_last_synced_detail_id"] = int(active_detail_id)

            id_list = df_all["id"].astype(int).tolist()
            default_cid = st.session_state.get("draft_selected_cid")
            if default_cid not in id_list:
                default_cid = int(active_detail_id) if (active_detail_id and int(active_detail_id) in id_list) else id_list[0]

            label_map = {
                int(r["id"]): f"{r['complaint_no']} · {str(r['product'])[:20]} ({r['status']})"
                for _, r in df_all.iterrows()
            }

            selected_cid = st.selectbox(
                "📌 Şikâyet Kaydı (Detay sayfasından otomatik seçilir):",
                options=id_list,
                index=id_list.index(default_cid),
                format_func=lambda x: label_map.get(x, str(x)),
                key="draft_cid_selectbox"
            )
            st.session_state["draft_selected_cid"] = int(selected_cid)
            current_item = get_complaint(int(selected_cid))
            ctx_preview = inspect_complaint_context(current_item)

            st.caption(
                f"**Müşteri:** `{ctx_preview['customer']}` | **Durum:** `{ctx_preview['status']}` | "
                f"**Öncelik:** `{ctx_preview['priority']}` | **Önerilen Tür:** `{ctx_preview['auto_purpose'][:28]}...`"
            )

            purpose_sel = st.selectbox(
                "🎯 Geri Dönüş Amacı:",
                RESPONSE_PURPOSES,
                index=RESPONSE_PURPOSES.index(st.session_state.get("draft_purpose", RESPONSE_PURPOSES[0]))
                if st.session_state.get("draft_purpose") in RESPONSE_PURPOSES else 0,
                key="ui_draft_purpose"
            )

            col_ch, col_tn, col_ln = st.columns(3)
            channel_sel = col_ch.selectbox("📡 Kanal:", CHANNELS, key="ui_draft_channel")
            tone_sel = col_tn.selectbox("🎭 Üslup:", TONES, key="ui_draft_tone")
            length_sel = col_ln.selectbox("📏 Uzunluk:", LENGTHS, index=1, key="ui_draft_length")

            extra_inst = st.text_input(
                "📝 Ek Talimat (İsteğe bağlı):",
                placeholder="Örn: Cihazın kargo kodu istensin, iade sözü verilmesin...",
                key="ui_draft_extra"
            )

            editor_key = f"draft_editor_text_{selected_cid}"
            meta_key = f"draft_last_meta_{selected_cid}"

            if editor_key not in st.session_state:
                init_res = generate_customer_response_draft(
                    current_item, purpose_sel, channel_sel, tone_sel, length_sel, extra_inst
                )
                st.session_state[editor_key] = init_res["draft_text"]
                st.session_state[meta_key] = init_res

            b1, b2, b3, b4, b5 = st.columns(5)
            trigger_regen = None
            if b1.button("🔄 Oluştur", help="Seçili ayarlarla metni yeniden oluştur", key="btn_gen_main"):
                trigger_regen = (purpose_sel, channel_sel, tone_sel, length_sel, False)
            if b2.button("✂️ Daha Kısa", help="Metni daha kısa ve öz hale getir", key="btn_gen_short"):
                trigger_regen = (purpose_sel, channel_sel, "Kısa ve Net", "Kısa", False)
            if b3.button("🤝 Empatik", help="Daha empatik ve yapıcı bir dil kullan", key="btn_gen_emp"):
                trigger_regen = (purpose_sel, channel_sel, "Empatik", length_sel, False)
            if b4.button("⚙️ Teknik", help="Daha teknik ve mühendislik odaklı yaz", key="btn_gen_tech"):
                trigger_regen = (purpose_sel, channel_sel, "Teknik", "Detaylı", False)
            if b5.button("🧠 AI ile Yaz", help="Aktif yapay zekâ modelini kullanarak talimatınıza göre doğal dille yeniden yaz", key="btn_gen_llm"):
                trigger_regen = (purpose_sel, channel_sel, tone_sel, length_sel, True)

            if trigger_regen:
                p_arg, ch_arg, tn_arg, ln_arg, llm_flag = trigger_regen
                new_res = generate_customer_response_draft(
                    current_item, p_arg, ch_arg, tn_arg, ln_arg, extra_inst, use_llm=llm_flag
                )
                st.session_state[editor_key] = new_res["draft_text"]
                st.session_state[meta_key] = new_res
                st.rerun()

            last_meta = st.session_state.get(meta_key) or generate_customer_response_draft(current_item)

            for w in last_meta.get("guardrail_warnings", []):
                st.warning(w)

            if ctx_preview["missing_fields"]:
                q_text_info = f"\n\n❓ *{ctx_preview['clarifying_questions'][0]}*" if ctx_preview["clarifying_questions"] else ""
                st.info(
                    f"🔍 **Eksik Kayıt Kontrolü:** `{', '.join(ctx_preview['missing_fields'])}` veritabanında bulunmadığı için **uydurulmamıştır**.{q_text_info}"
                )

            edited_text = st.text_area(
                "✏️ Oluşturulan Müşteri Geri Dönüş Taslağı (Düzenlenebilir):",
                height=420 if is_fs else 300,
                key=editor_key,
                help="Metin üzerinde yaptığınız düzenlemeler otomatik korunur. Onayınız olmadan hiçbir mesaj gönderilmez."
            )

            act_c1, act_c2 = st.columns(2)
            with act_c1:
                render_copy_button(edited_text, f"cid_{selected_cid}")
            with act_c2:
                if st.button("💾 Taslak Olarak Kaydet", key="btn_save_draft_db", use_container_width=True):
                    draft_id = save_draft_to_db(
                        cid=int(selected_cid),
                        c_no=ctx_preview["complaint_no"],
                        purpose=last_meta.get("resolved_purpose", purpose_sel),
                        channel=last_meta.get("channel", channel_sel),
                        tone=last_meta.get("tone", tone_sel),
                        length_opt=last_meta.get("length_opt", length_sel),
                        extra_inst=extra_inst,
                        subject=last_meta.get("subject", ""),
                        body_text=edited_text,
                        source_summary={
                            "complaint_no": ctx_preview["complaint_no"],
                            "status": ctx_preview["status"],
                            "priority": ctx_preview["priority"],
                            "has_root_cause": ctx_preview["has_root_cause"],
                            "has_action_plan": ctx_preview["has_action_plan"],
                            "missing_fields": ctx_preview["missing_fields"]
                        },
                        username=username
                    )
                    st.success(f"✅ Taslak #{draft_id} `{ctx_preview['complaint_no']}` kaydına işlendi! (Otomatik gönderim yapılmadı, kayıt kapatılmadı.)")

            with st.expander("🔍 Metnin Dayandığı Şikâyet Verileri & Kayıtlı Taslaklar"):
                plan_preview_str = (ctx_preview["action_plan"][:140] + "...") if ctx_preview["action_plan"] else "Veritabanında kayıtlı değil"
                corp_label_str = "Kurumsal" if ctx_preview["is_corporate"] else "Bireysel"
                st.markdown(
                    f"- **Şikâyet No & Tarih:** `{ctx_preview['complaint_no']}` ({ctx_preview['created_date']} · {ctx_preview['elapsed_days']} gün)\n"
                    f"- **Müşteri & Muhatap:** `{ctx_preview['customer']}` / `{ctx_preview['contact'] or '-'}` ({corp_label_str})\n"
                    f"- **İletişim:** `{ctx_preview['email']}` · `{ctx_preview['phone']}`\n"
                    f"- **Ürün & Seri/MDR No:** `{ctx_preview['product']}` · `{ctx_preview['order_no'] or 'Belirtilmemiş'}`\n"
                    f"- **Durum & Geçerlilik:** `{ctx_preview['status']}` · `{ctx_preview['validity']}`\n"
                    f"- **Doğrulanmış Kök Neden:** `{ctx_preview['root_cause'] or 'Veritabanında kayıtlı değil'}`\n"
                    f"- **Teknik Servis Planı:** `{plan_preview_str}`\n"
                    f"- **Önceki İletişim Sayısı:** `{ctx_preview['prev_contacts_count']}` kayıt | **Açık DÖF:** `{len(ctx_preview['open_actions'])}` adet"
                )
                saved_list = get_saved_drafts(int(selected_cid))
                if saved_list:
                    st.divider()
                    st.markdown("**📂 Bu Şikâyet İçin Kaydedilmiş Son Taslaklar:**")
                    for d_row in saved_list[:3]:
                        st.caption(f"• **#{d_row['id']}** ({d_row['created_at'][:16]}) - {d_row['purpose']} [{d_row['channel']} / {d_row['tone']}] - Kaydeden: `{d_row['created_by']}`")
                        if st.button(f"🔄 Taslak #{d_row['id']} Metnini Editöre Yükle", key=f"load_dr_{d_row['id']}"):
                            st.session_state[editor_key] = d_row["draft_text"]
                            st.rerun()

            with st.expander("🏢 Kurumsal İmza ve Standart İletişim Ayarları"):
                curr_comp = get_company_settings()
                c_name = st.text_input("Şirket Unvanı", value=curr_comp["company_name"], key="cfg_cname")
                c_sign = st.text_area("Standart Kapanış / İmza", value=curr_comp["closing_template"], height=68, key="cfg_csign")
                c_info = st.text_input("İletişim Bilgileri", value=curr_comp["contact_info"], key="cfg_cinfo")
                if st.button("💾 Kurumsal İletişim Ayarlarını Kaydet", key="btn_save_comp_cfg"):
                    save_company_settings({
                        "company_name": c_name.strip(),
                        "closing_template": c_sign.strip(),
                        "contact_info": c_info.strip()
                    })
                    st.success("Kurumsal imza ayarları güncellendi.")

        # SEKME 3: GELİŞMİŞ KALİTE ANALİZ MOTORU
        with tab_qe:
            render_quality_engine_tab(user)