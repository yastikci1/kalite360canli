import json
import streamlit as st
import pandas as pd
from datetime import datetime
from db import db, actions, communications, log
from utils import query_ollama_chat

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


def save_draft_to_db(
    cid: int, c_no: str, purpose: str, channel: str, tone: str, length_opt: str,
    extra_inst: str, subject: str, body_text: str, source_summary: dict, username: str
):
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


def is_user_authorized(user: dict) -> tuple[bool, str]:
    if not user or not isinstance(user, dict) or not user.get("username"):
        return False, "Müşteri bilgilerine ve taslak oluşturucuya erişmek için yetkili bir hesapla giriş yapmalısınız."
    role = user.get("role", "")
    if role not in AUTHORIZED_ROLES:
        return False, f"'{role}' rolünün müşteri iletişim bilgilerine erişim yetkisi bulunmamaktadır."
    return True, "Yetkili"


def _extract_cause(text):
    t = str(text or "").lower()
    if any(k in t for k in ["overheat", "hot", "ısınma", "isinma", "temperature", "sıcaklık", "sicaklik"]):
        return "Aşırı Isınma / Termal Sapma"
    if any(k in t for k in ["bearing", "worn", "aşınma", "asinma", "rulman", "bilya"]):
        return "Rulman / Mekanik Aşınma"
    if any(k in t for k in ["stuck", "collet", "sıkışma", "sikisma", "kilitlen", "pens"]):
        return "Mekanik Sıkışma / Pens Kilitlenmesi"
    if any(k in t for k in ["cable", "wire", "kablo", "temassız", "temassiz"]):
        return "Kablo / Bağlantı Temassızlığı"
    if any(k in t for k in ["corrosion", "corroded", "oksit", "pas"]):
        return "Korozyon / Oksitlenme"
    if any(k in t for k in ["leak", "hipot", "shock", "kaçak", "kacak", "elektrik"]):
        return "Elektriksel Kaçak / İzolasyon"
    if any(k in t for k in ["stop", "not work", "çalışmı", "calismi", "durdu"]):
        return "Ani Durma / Fonksiyon Kaybı"
    if any(k in t for k in ["noise", "ses", "rough", "titreşim", "titresim"]):
        return "Anormal Ses / Titreşim"
    if any(k in t for k in ["break", "broken", "cracked", "kırık", "kirik", "çatlak", "catlak", "koptu"]):
        return "Fiziksel Kırılma / Çatlak"
    if any(k in t for k in ["tolerans", "ölçü", "olcu", "çap", "cap", "boyut"]):
        return "Ölçü / Tolerans Uygunsuzluğu"
    if any(k in t for k in ["teslim", "gecik", "paket", "ambalaj"]):
        return "Sevkiyat / Paketleme Problemi"
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