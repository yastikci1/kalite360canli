import re
import difflib
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from db import complaints, db, get_complaint
from ai_quality_engine import detect_and_handle_quality_query
from ai_response_drafter import (
    RESPONSE_PURPOSES,
    is_user_authorized,
    _extract_cause,
    _is_meaningful_field,
    inspect_complaint_context,
    generate_customer_response_draft,
)
from utils import get_ollama_status, query_ollama_chat, get_last_ai_error


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


def _extract_nlp_filters(prompt: str, df: pd.DataFrame) -> dict:
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

    # Eğer kullanıcı "pareto analizi nedir" gibi genel bir soru soruyorsa tüm veritabanını koru
    is_conceptual_pareto = "pareto" in q_ascii and any(w in q_ascii for w in ["nedir", "ne demek", "nasil", "analizi"])
    if not is_conceptual_pareto:
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


def _build_comprehensive_knowledge_pack(
    df: pd.DataFrame,
    fdf: pd.DataFrame,
    active_filters: list,
    active_item: dict = None
) -> str:
    """
    Token kotasını (TPM) yormadan en yüksek doğrulukta yanıt üretmek için
    kompakt ve zenginleştirilmiş analitik veri paketi oluşturur.
    """
    total_all = len(df)
    open_all = len(df[df["status"] != "Kapalı"])
    closed_all = total_all - open_all

    f_total = len(fdf)
    f_open = len(fdf[fdf["status"] != "Kapalı"])
    f_closed = f_total - f_open

    prod_breakdown = "\n".join([f"  - {k}: {v} şikâyet (%{(v/f_total*100):.1f})" for k, v in fdf["product"].value_counts().head(8).items()]) if not fdf.empty else "  - Kayıt yok"
    cust_breakdown = "\n".join([f"  - {k}: {v} şikâyet (%{(v/f_total*100):.1f})" for k, v in fdf["customer"].value_counts().head(6).items()]) if not fdf.empty else "  - Kayıt yok"
    cause_breakdown = "\n".join([f"  - {k}: {v} vaka (%{(v/f_total*100):.1f})" for k, v in fdf["cause_class"].value_counts().head(10).items()]) if not fdf.empty else "  - Kayıt yok"
    status_breakdown = ", ".join([f"{k}: {v}" for k, v in fdf["status"].value_counts().items()]) if not fdf.empty else "Yok"
    prio_breakdown = ", ".join([f"{k}: {v}" for k, v in fdf["priority"].value_counts().items()]) if not fdf.empty else "Yok"
    validity_breakdown = ", ".join([f"{k}: {v}" for k, v in fdf["validity"].fillna("Değerlendirilmedi").value_counts().items()]) if not fdf.empty else "Yok"

    rc_entries = []
    service_eval_entries = []
    if not fdf.empty:
        for _, r in fdf.head(30).iterrows():
            cno = r.get("complaint_no")
            rc_val = str(r.get("root_cause") or "").strip()
            ap_val = str(r.get("action_plan") or "").strip()
            if _is_meaningful_field(rc_val) and len(rc_entries) < 5:
                rc_entries.append(f"  - [{cno}] ({r.get('product')}): {rc_val[:140]}")
            if _is_meaningful_field(ap_val) and len(service_eval_entries) < 5:
                service_eval_entries.append(f"  - [{cno}] ({r.get('product')} - {r.get('cause_class')}): {ap_val[:150]}")

    rc_md = "\n".join(rc_entries) if rc_entries else "  - Kayıtlarda ayrı 'root_cause' alanı çoğunlukla boş; teknik kök neden bulguları aşağıdaki Teknik Servis / Üretici İnceleme Bulguları (action_plan) içinde yer almaktadır."
    service_eval_md = "\n".join(service_eval_entries) if service_eval_entries else "  - Teknik servis bulgusu girilmemiş."

    max_dt = fdf["dt"].max() if not fdf.empty else pd.NaT
    ref_date = max_dt if pd.notna(max_dt) else datetime.now()
    last_30 = len(fdf[fdf["dt"] >= (ref_date - timedelta(days=30))]) if not fdf.empty else 0
    prev_30 = len(fdf[(fdf["dt"] < (ref_date - timedelta(days=30))) & (fdf["dt"] >= (ref_date - timedelta(days=60)))]) if not fdf.empty else 0

    with db() as conn:
        act_rows = conn.execute(
            "SELECT a.title, a.assignee, a.due_date, a.status, c.complaint_no, c.product "
            "FROM actions a LEFT JOIN complaints c ON a.complaint_id = c.id "
            "WHERE a.status != 'Kapalı' ORDER BY a.due_date ASC LIMIT 6"
        ).fetchall()
    dof_lines = "\n".join([
        f"  - [{r['complaint_no'] or '-'}] {r['product'] or '-'}: {r['title']} (Sorumlu: {r['assignee']}, Termin: {r['due_date']}, Durum: {r['status']})"
        for r in act_rows
    ]) if act_rows else "  - Sistemde açık DÖF/aksiyon kaydı bulunmuyor."

    sample_records = []
    for _, r in fdf.head(5).iterrows():
        sample_records.append(
            f"  - {r.get('complaint_no')} ({str(r.get('created_at') or '')[:10]}): Müşteri={r.get('customer')}, "
            f"Ürün={r.get('product')}, Arıza={r.get('cause_class')}, Öncelik={r.get('priority')}, Durum={r.get('status')}"
        )
    samples_md = "\n".join(sample_records) if sample_records else "  - Eşleşen kayıt yok."

    active_comp_md = ""
    if active_item:
        ctx_a = inspect_complaint_context(active_item)
        active_comp_md = (
            f"\n=== ŞU AN SEÇİLİ / AKTİF ŞİKÂYET KAYDI ({ctx_a['complaint_no']}) ===\n"
            f"- Müşteri: {ctx_a['customer']} | Ürün: {ctx_a['product']} | Seri/Lot: {ctx_a['order_no'] or 'Belirtilmemiş'}\n"
            f"- Kategori & Arıza Tipi: {ctx_a['category']} / {ctx_a['issue_topic']}\n"
            f"- Durum: {ctx_a['status']} | Öncelik: {ctx_a['priority']} | Geçerlilik: {ctx_a['validity']}\n"
            f"- Şikâyet Metni: {ctx_a['description'][:300]}\n"
            f"- Doğrulanmış Kök Neden: {ctx_a['root_cause'] or 'Ayrı kök neden alanına girilmemiş (Aşağıdaki servis metnine bakınız)'}\n"
            f"- Çözüm / Üretici Teknik İnceleme Metni (action_plan): {ctx_a['action_plan'][:350] or 'Henüz çözüm planı girilmemiş'}"
        )

    return f"""=== KALİTE360 VERİTABANI CANLI VERİ PAKETİ ===
- Tüm Veritabanı Toplamı: {total_all} şikâyet ({open_all} Açık, {closed_all} Kapalı)
- Uygulanan Filtreler: {', '.join(active_filters) if active_filters else 'Filtre yok (Tüm Veritabanı)'}
- Filtreye Uyan Kayıt Sayısı: {f_total} şikâyet ({f_open} Açık, {f_closed} Kapalı)
- Son 30 Gün Vaka Sayısı: {last_30} adet (Önceki 30 gün: {prev_30} adet)
- Durum Dağılımı: {status_breakdown} | Öncelik Dağılımı: {prio_breakdown}
- Geçerlilik Dağılımı: {validity_breakdown}

[ÜRÜN / MODEL DAĞILIMI (İlk 8)]:
{prod_breakdown}

[MÜŞTERİ / KURUM DAĞILIMI (İlk 6)]:
{cust_breakdown}

[HATA / ARIZA NEDENİ DAĞILIMI (Pareto 80/20 Dağılımı)]:
{cause_breakdown}

[KAYITLI KÖK NEDENLER (root_cause)]:
{rc_md}

[TEKNİK SERVİS & ÜRETİCİ LABORATUVAR BULGULARI (action_plan)]:
{service_eval_md}

[AÇIK DÖF / AKSİYON GÖREVLERİ]:
{dof_lines}

[ÖRNEK ŞİKÂYET KAYITLARI]:
{samples_md}
{active_comp_md}"""


def _generate_agentic_ai_response(user_prompt: str, knowledge_pack: str) -> str | None:
    if not st.session_state.get("use_ollama_ai", True):
        return None

    status = get_ollama_status()
    if not status["available"]:
        return None

    system_prompt = """Sen Kalite360 Fabrika & Medikal Sistemler A.Ş.'nin Yapay Zekâ Kalite Güvence Başmühendisisin.
Kullanıcı seninle günlük konuşma diliyle, samimi, bazen çok kısa ("pareto analizi nedir", "kök neden nedir", "neden ısınıyor", "bunu nasıl çözeriz") veya devrik cümlelerle konuşabilir.

KESİN ÇALIŞMA KURALLARIN:
1. DOĞRUDAN VE AKILLI YANIT VER: Kullanıcının sorusundaki asıl niyeti anla. Kavramsal veya analitik bir soru sorduğunda hem kavramı açıkla hem de Kalite360 veritabanındaki canlı verilerle (Pareto dağılımı, seçili şikâyet bulguları, ürün/arıza istatistikleri) birleştirerek yanıtla.
2. %100 GERÇEK VERİ KULLAN: Yanıtındaki tüm sayıları, şikâyet numaralarını (`SK-...`), ürün ve müşteri isimlerini sana verilen `=== KALİTE360 VERİTABANI CANLI VERİ PAKETİ ===` içinden al. Veritabanında olmayan bir sayıyı uydurma.
3. MÜHENDİSLİK ZEKÂSI: Kullanıcı yorum, tavsiye, kök neden, Pareto, DÖF/CAPA, 8D, FMEA veya "nasıl çözeriz" diye sorarsa ISO 9001 ve ISO 13485 standartlarına hâkim deneyimli bir Kalite Mühendisi gibi somut, uygulanabilir çözümler sun.
4. FORMAT: Yanıtını okunaklı Markdown başlıkları, maddeler ve gerektiğinde özet tablolarla akıcı bir Türkçe ile yaz."""

    messages = [{"role": "system", "content": system_prompt}]

    recent_hist = (st.session_state.get("ai_chat_history") or [])[-4:-1]
    for h in recent_hist:
        r_role = h.get("role", "user")
        r_content = str(h.get("content", "")).strip()
        if r_role in ["user", "assistant"] and r_content and not r_content.startswith("Merhaba! 👋"):
            messages.append({"role": r_role, "content": r_content[:500]})

    user_message = (
        f"{knowledge_pack}\n\n"
        f"=== KULLANICININ ŞU ANKİ SORUSU ===\n"
        f"{user_prompt}\n\n"
        f"Lütfen yukarıdaki canlı veritabanı verilerine dayanarak kullanıcının sorusunu doğrudan, doğal ve kapsamlı bir Kalite Güvence Başmühendisi gibi yanıtla:"
    )
    messages.append({"role": "user", "content": user_message})

    model_to_use = st.session_state.get("selected_ollama_model") or status["active_model"]
    return query_ollama_chat(messages, model=model_to_use, temperature=0.25, timeout=45)


def _fallback_deterministic_answer(
    raw_prompt: str,
    df: pd.DataFrame,
    fdf: pd.DataFrame,
    active_filters: list,
    explicit_item: dict = None,
    focus_item: dict = None
) -> str:
    q = _normalize_tr(raw_prompt)
    err_note = get_last_ai_error()
    err_banner = f"> ℹ️ *Not: Bulut AI bağlantı uyarısı (`{err_note}`). Yanıt yerel Kalite360 Analitik Motoru tarafından üretildi.*\n\n" if err_note else ""
    filter_badge = f"**Uygulanan Filtreler:** `{' | '.join(active_filters)}`\n\n" if active_filters else ""

    if explicit_item and not _fuzzy_has(q, ["kök neden", "neden", "çözüm", "ne yapılmalı", "nasıl çöz", "pareto"]):
        ctx = inspect_complaint_context(explicit_item)
        return (
            f"{err_banner}📄 **`{ctx['complaint_no']}` Şikâyet Kaydı Bilgileri:**\n\n"
            f"- **Müşteri / Kurum:** {ctx['customer']} ({ctx['contact'] or '-'})\n"
            f"- **Ürün & Seri/Lot No:** {ctx['product']} · `{ctx['order_no'] or 'Belirtilmemiş'}`\n"
            f"- **Kategori & Arıza Tipi:** {ctx['category']} / `{ctx['issue_topic']}`\n"
            f"- **Durum & Öncelik:** `{ctx['status']}` · `{ctx['priority']}` (Sorumlu: `{explicit_item.get('owner') or '-'}`)\n"
            f"- **Şikâyet Açıklaması:** {ctx['description'] or '-'}\n"
            f"- **Kök Neden:** {ctx['root_cause'] or 'Henüz ayrı kök neden girilmemiş'}\n"
            f"- **Teknik Servis / Çözüm Planı:** {ctx['action_plan'] or 'Henüz girilmemiş'}"
        )

    if fdf.empty:
        return (
            f"{err_banner}🔍 **Eşleşen Kayıt Bulunamadı:**\n\n{filter_badge}"
            f"Belirttiğiniz kriterlere uygun şikâyet kaydı veritabanında mevcut değil."
        )

    total_f = len(fdf)
    open_f = len(fdf[fdf["status"] != "Kapalı"])

    if _fuzzy_has(q, ["kök neden", "kok neden", "neden kaynaklan", "sebebi ne", "neden ısını", "neden bozul", "hata neden", "arıza neden", "en sık", "pareto", "hangi hata", "hangi arıza", "nasıl çözeriz", "çözüm nedir"]):
        causes = fdf["cause_class"].value_counts()
        cum_pct = 0.0
        rows_list = []
        for c, cnt in causes.head(8).items():
            pct = (cnt / total_f * 100) if total_f else 0.0
            cum_pct += pct
            rows_list.append(f"| {c} | {cnt} | %{pct:.1f} | %{min(100.0, cum_pct):.1f} |")
        rows_md = "\n".join(rows_list)

        target = explicit_item or focus_item
        active_rc_section = ""
        if target:
            ctx_t = inspect_complaint_context(target)
            rc_text = ctx_t["root_cause"] if ctx_t["has_root_cause"] else (
                ctx_t["action_plan"][:300] if ctx_t["has_action_plan"] else "Veritabanında bu kayıt için henüz doğrulanmış laboratuvar kök nedeni girilmemiştir."
            )
            active_rc_section = (
                f"#### 🎯 Seçili Şikâyet (`{ctx_t['complaint_no']}` - {ctx_t['product']}) Kök Neden Durumu\n"
                f"- **Bildirilen Arıza Tipi:** `{ctx_t['issue_topic']}` (Durum: `{ctx_t['status']}`, Öncelik: `{ctx_t['priority']}`)\n"
                f"- **Kayıtlı Kök Neden / Teknik Servis Tespiti:** {rc_text}\n\n"
            )

        top_cause = causes.index[0] if not causes.empty else "Teknik Performans Bildirimi"
        return (
            f"{err_banner}### 📊 Pareto (80/20) ve Kök Neden Dağılım Analizi ({total_f} Kayıt)\n\n"
            f"**Pareto Analizi (80/20 Kuralı):** Kalite yönetiminde toplam şikâyetlerin yaklaşık %80'inin, en sık tekrarlanan %20'lik temel hata türlerinden kaynaklandığını gösteren önceliklendirme yöntemidir.\n\n"
            f"{filter_badge}"
            f"{active_rc_section}"
            f"#### 📈 Kalite360 Veritabanı Canlı Pareto Tablosu\n"
            f"| Kök Neden / Arıza Kategorisi | Vaka Sayısı | Oran | Kümülatif Oran |\n|---|---|---|---|\n{rows_md}\n\n"
            f"#### 🛠️ Kalite Mühendisliği Değerlendirmesi\n"
            f"- **Birincil Odak (`{top_cause}`):** Vakaların en büyük kısmı bu kategoride yoğunlaşmaktadır. Yüksek devirli medikal/mekanik ekipmanlarda **rulman yatak aşınması, yağlama kaybı, otoklav nem korozyonu** ve **pens (collet) mekanizmasında aşırı yanal yüklenme** ana kök neden hipotezleridir."
        )

    if _fuzzy_has(q, ["hangi müşteri", "hangi firma", "müşteri dağılımı"]):
        custs = fdf["customer"].value_counts()
        rows_md = "\n".join([f"| {c} | {cnt} | %{(cnt / total_f * 100):.1f} |" for c, cnt in custs.head(8).items()])
        return f"{err_banner}🏢 **Müşteri Bazlı Şikâyet Dağılımı ({total_f} Kayıt):**\n\n{filter_badge}| Müşteri | Şikâyet Sayısı | Oran |\n|---|---|---|\n{rows_md}"

    if _fuzzy_has(q, ["hangi ürün", "hangi model", "ürün dağılımı", "en çok şikayet"]):
        prods = fdf["product"].value_counts()
        rows_md = "\n".join([f"| {p} | {cnt} | %{(cnt / total_f * 100):.1f} |" for p, cnt in prods.head(8).items()])
        return f"{err_banner}📊 **Ürün Bazlı Şikâyet Dağılımı ({total_f} Kayıt):**\n\n{filter_badge}| Ürün / Model | Şikâyet Sayısı | Oran |\n|---|---|---|\n{rows_md}"

    rows_md = "\n".join([
        f"| `{r['complaint_no']}` | {str(r['customer'])[:18]} | {str(r['product'])[:20]} | {r['cause_class'][:22]} | {r['priority']} | {r['status']} |"
        for _, r in fdf.head(8).iterrows()
    ])
    return (
        f"{err_banner}📋 **Sorgu Sonucu: Toplam `{total_f}` Kayıt (`{open_f}` Açık):**\n\n"
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

    target_for_qe = _resolve_target_complaint(raw_prompt)
    qe_response = detect_and_handle_quality_query(raw_prompt, target_for_qe, user)
    if qe_response:
        return qe_response

    explicit_item, is_explicit_single = _extract_explicit_complaint_from_prompt(raw_prompt, df)
    nlp_res = _extract_nlp_filters(raw_prompt, df)
    fdf = nlp_res["filtered_df"]
    active_filters = nlp_res["active_filters"]

    focus_item = explicit_item if is_explicit_single else target_for_qe
    knowledge_pack = _build_comprehensive_knowledge_pack(
        df=df,
        fdf=fdf,
        active_filters=active_filters,
        active_item=focus_item
    )

    ai_direct_answer = _generate_agentic_ai_response(raw_prompt, knowledge_pack)
    if ai_direct_answer:
        return ai_direct_answer

    return _fallback_deterministic_answer(
        raw_prompt=raw_prompt,
        df=df,
        fdf=fdf,
        active_filters=active_filters,
        explicit_item=explicit_item if is_explicit_single else None,
        focus_item=focus_item
    )