import io
import streamlit as st
import pandas as pd
import re
from db import complaints
from utils import classify_validity
from services.ai_service import is_translation_enabled, ensure_translation_columns
from ai_quality_engine import extract_failure_mode, determine_complaint_validity

def _clean_negations(text: str) -> str:
    t = str(text or "").lower()
    if "medtronic submits this report" in t:
        t = t.split("medtronic submits this report")[0]
    neg_patterns = [
        r"\bno\s+(?:overheating|heat|thermal|corrosion|rust|defect|failure|problem|fault|damage|noise|vibration|leak|leakage|short)\b",
        r"\bdid\s+not\s+(?:overheat|fail|break|leak|malfunction)\b",
        r"\bwithout\s+(?:overheating|damage|defect|issue)\b",
        r"\bpassed\s+(?:all\s+)?(?:hipot|electrical|functional|temperature|thermal)\b",
        r"\btemperature\s+was\s+measured\s+to\s+be\s+within\b",
    ]
    for pat in neg_patterns:
        t = re.sub(pat, " ", t)
    return t

def extract_root_cause(text: str) -> str:
    t = str(text or "").lower().strip()
    if not t or "fda maude kaydında olay metni belirtilmemiş" in t:
        return "Diğer / Tanımlanamayan"

    if any(k in t for k in [
        "overheat", "over-heat", "hot", "extremely warm", "warm to touch", "thermal",
        "temperature", "burn", "burned", "burnt", "scald", "heat", "cooling",
        "ısınma", "ısındı", "sıcaklık", "sıcak", "termal", "yanık", "aşırı ısı"
    ]):
        return "Aşırı Isınma"

    if any(k in t for k in [
        "break", "broke", "broken", "crack", "cracked", "fracture", "fractured",
        "snap", "snapped", "shatter", "split", "severed", "sheared", "chipped",
        "detach", "detached", "detachment", "separat", "came apart", "fell apart",
        "fell off", "dislodg", "fragment", "piece broke", "bent", "deform", "dent",
        "tip broke", "bur broke", "burr broke", "flute broke", "leg broke", "footplate broke",
        "kırık", "kırıl", "çatlak", "çatla", "koptu", "kopma", "ayrıl", "parçalan", "yamul", "ezil"
    ]):
        return "Fiziksel Kırılma / Çatlak"

    if any(k in t for k in [
        "bearing", "worn", "wear", "rulman", "bilya", "aşınma", "aşındı", "dağılma",
        "preventive maintenance", "inadequate maintenance", "lack of maintenance",
        "lubricat", "grease", "oiling", "friction", "spindle", "rotor", "shaft worn",
        "vane", "gear worn", "scoring", "galling", "rubbing", "fatigue", "overhaul",
        "bakım eksik", "yağlama", "mekanik yıpranma", "mil aşın"
    ]):
        return "Rulman / Mekanik Aşınma"

    if any(k in t for k in [
        "stuck", "collet", "sıkışma", "sıkıştı", "kilitlen", "pens", "seized", "seizure",
        "jam", "jammed", "bind", "binding", "locked", "lock", "unlock", "chuck", "detent",
        "hold the bur", "hold the tool", "doesn't hold", "did not hold", "would not hold",
        "not hold", "slip", "slipped", "slipping", "bur came out", "burr came out",
        "insert", "insertion", "unable to remove", "difficult to remove", "removal",
        "extract", "release", "attach", "connect", "disconnect", "disassembl", "assembl",
        "fit", "seat", "seated", "engage", "thread", "stripped", "unscrew", "tight", "stiff",
        "takılamadı", "çıkarılamadı", "uç tutmuyor", "uç sıkıştı"
    ]):
        return "Mekanik Sıkışma / Kilitlenme"

    if any(k in t for k in [
        "noise", "noisy", "ses", "rough", "vibrat", "titreşim", "vibrasyon",
        "wobbl", "runout", "run-out", "eccentric", "salgı", "yalpalama",
        "chatter", "rattl", "grind", "squeal", "whine", "screech", "loud",
        "audible", "unbalanced", "unstable", "jumping", "skipping"
    ]):
        return "Anormal Ses / Titreşim"

    if any(k in t for k in [
        "cable", "wire", "wiring", "cord", "kablo", "temassız", "connector",
        "plug", "pin", "socket", "receptacle", "hose", "tube", "tubing",
        "pneumatic", "air leak", "hava kaçağı", "frayed", "exposed wire",
        "strain relief", "kink", "pedal", "footswitch", "footpedal", "handpiece cord",
        "konnektör", "soket", "bağlantı kopuk"
    ]):
        return "Kablo / Temassızlık"

    if any(k in t for k in [
        "hipot", "hi-pot", "shock", "electrical", "electric", "kaçak", "elektrik",
        "short circuit", "shorted", "open circuit", "current", "voltage", "ground",
        "insulation", "dielectric", "spark", "arc", "arcing", "smoke", "smell", "odor",
        "fuse", "board", "pcb", "sensor", "hall", "stator", "coil", "winding",
        "resistance", "ohm", "error code", "fault code", "alarm", "console error",
        "kısa devre", "izolasyon", "sensör", "stator", "bobin", "hata kodu"
    ]):
        return "Elektriksel Hata / Kaçak"

    if any(k in t for k in [
        "corrosion", "corroded", "corros", "oksit", "pas", "paslanma", "rust", "rusted",
        "oxid", "pitting", "pitted", "stain", "discolor", "faded", "peel", "peeling",
        "flak", "coating", "anodiz", "autoclave", "steriliz", "washer", "chemical",
        "moisture", "water ingress", "fluid ingress", "seal", "o-ring", "gasket",
        "leak", "leaking", "sızdırma", "korozyon", "sterilizasyon"
    ]):
        return "Korozyon / Oksitlenme"

    if any(k in t for k in [
        "stop", "stopped", "stopping", "not work", "did not work", "does not work",
        "would not work", "failed to work", "failed to run", "would not run",
        "did not run", "not run", "fail to operate", "failed to operate",
        "did not operate", "would not operate", "inoperative", "non-functional",
        "not function", "failed to function", "ceased", "cut out", "shut off",
        "shut down", "turn on", "power on", "would not start", "did not start",
        "spin", "rotate", "rotation", "no rotation", "slow", "weak", "low power",
        "loss of power", "no power", "lack of power", "torque", "rpm", "speed",
        "stall", "stalled", "stalling", "intermittent", "hesitat", "dead",
        "malfunction", "defective", "failed during", "failure",
        "çalışmı", "çalışmadı", "durdu", "dönmüyor", "devir", "güç kaybı", "kesinti", "arızalandı"
    ]):
        return "Ani Durma / Çalışmama"

    return "Diğer / Tanımlanamayan"

def resolve_smart_root_cause(row) -> str:
    desc_combined = f"{row.get('description') or ''} {row.get('tr_description') or ''}"
    cause = extract_root_cause(desc_combined)
    if cause != "Diğer / Tanımlanamayan":
        return cause

    rc_raw = f"{row.get('root_cause') or ''} {row.get('action_plan') or ''} {row.get('tr_action_plan') or ''}"
    rc_cleaned = _clean_negations(rc_raw)
    cause = extract_root_cause(rc_cleaned)
    if cause != "Diğer / Tanımlanamayan":
        return cause

    full_context = f"{desc_combined} {rc_cleaned}".lower()
    if any(k in full_context for k in ["bur", "burr", "attachment", "footplate", "guard", "hood", "collet", "tube", "chuck", "sleeve"]):
        return "Mekanik Sıkışma / Kilitlenme"
    if any(k in full_context for k in ["service", "repaired", "replaced", "overhaul", "rebuild", "maintenance", "worn", "age", "wear"]):
        return "Rulman / Mekanik Aşınma"
    if any(k in full_context for k in ["damage", "damaged", "broken", "fracture", "bent", "dent", "missing", "tip"]):
        return "Fiziksel Kırılma / Çatlak"
    if any(k in full_context for k in ["handpiece", "motor", "drill", "console", "pedal"]):
        return "Ani Durma / Çalışmama"

    return "Diğer / Tanımlanamayan"

def extract_model_group(prod_str):
    if not prod_str: return "Bilinmiyor"
    match = re.search(r'\(([^)]+)\)', str(prod_str))
    if match:
        return match.group(1).strip().upper()
    return str(prod_str).split()[0].upper() if prod_str else "BİLİNMİYOR"

def render():
    ensure_translation_columns()
    df = complaints()
    if df.empty: 
        st.info("Kayıt bulunamadı.")
        return
        
    tr_active = is_translation_enabled()

    # Akıllı sütun eşleme ve hesaplama mantığı
    df["Şikâyet Doğruluğu"] = df.apply(classify_validity, axis=1)
    df["Hata Kategorisi"] = df.apply(resolve_smart_root_cause, axis=1)
    df["Model Grubu"] = df["product"].apply(extract_model_group)
    
    c1, c2, c3, c4 = st.columns([1.5, 1.5, 1, 1])
    
    q = c1.text_input("Ara", placeholder="No veya açıklama ara...")
    
    urun_listesi = ["Tümü"] + sorted(df["Model Grubu"].dropna().unique().tolist())
    prod_f = c2.selectbox("Ürün Modeli", urun_listesi)
    
    dogruluk_listesi = ["Tümü", "Şikâyet Doğru", "Şikâyet Yanlış", "İnceleme Yapılıyor", "Çözüm Belirtilmemiş"]
    val_f = c3.selectbox("Şikâyet Doğruluğu", dogruluk_listesi)
    
    hata_listesi = ["Tümü", "Aşırı Isınma", "Rulman / Mekanik Aşınma", "Mekanik Sıkışma / Kilitlenme", "Kablo / Temassızlık", "Korozyon / Oksitlenme", "Elektriksel Hata / Kaçak", "Ani Durma / Çalışmama", "Anormal Ses / Titreşim", "Fiziksel Kırılma / Çatlak", "Diğer / Tanımlanamayan"]
    cause_f = c4.selectbox("Hata Kategorisi", hata_listesi)
    
    if q: 
        df = df[df.astype(str).apply(lambda col: col.str.contains(q, case=False, na=False)).any(axis=1)]
    if prod_f != "Tümü": 
        df = df[df["Model Grubu"] == prod_f]
    if val_f != "Tümü": 
        df = df[df["Şikâyet Doğruluğu"] == val_f]
    if cause_f != "Tümü": 
        df = df[df["Hata Kategorisi"] == cause_f]
        
    df = df.copy()
    
    df["ID"] = df["id"]
    df["Şikâyet No"] = df["complaint_no"]
    df["Kayıt Tarihi"] = df["created_at"].astype(str).str.slice(0, 10)
    df["Ürün"] = df["Model Grubu"]

    if tr_active and "tr_description" in df.columns:
        df["Müşteri Şikâyeti"] = df.apply(
            lambda r: r["tr_description"] if pd.notna(r.get("tr_description")) and str(r.get("tr_description")).strip() else (r.get("description") or "-"),
            axis=1
        )
    else:
        df["Müşteri Şikâyeti"] = df["description"].fillna("-")
    
    def resolve_solution(row):
        if tr_active and pd.notna(row.get("tr_action_plan")) and str(row.get("tr_action_plan")).strip():
            plan = str(row.get("tr_action_plan")).strip()
        else:
            plan = str(row.get("action_plan")).strip() if pd.notna(row.get("action_plan")) else ""
        at = str(row.get("action_type")).strip() if pd.notna(row.get("action_type")) else ""
        if plan: return f"{at}: {plan}" if at else plan
        if at: return at
        req = str(row.get("requested")).strip() if pd.notna(row.get("requested")) else ""
        if req: return req
        return "Teknik servis incelemesi / Çözüm bekleniyor"

    df["Üretilen Çözüm"] = df.apply(resolve_solution, axis=1)

    display_cols = ["ID", "Şikâyet No", "Kayıt Tarihi", "Ürün", "Hata Kategorisi", "Şikâyet Doğruluğu", "Müşteri Şikâyeti", "Üretilen Çözüm"]
    show = df[display_cols].copy()
    
    st.caption("Detayını açmak için tablodaki şikâyet satırına tıklayabilirsiniz.")
    
    try:
        event = st.dataframe(show, use_container_width=True, hide_index=True, on_select="rerun", selection_mode="single-row")
        selected_rows = event.selection.rows if hasattr(event, "selection") else []
        if selected_rows:
            st.session_state.detail_id = int(show.iloc[selected_rows[0]]["ID"])
            st.session_state.pending_page = "Şikâyet Detayı"
            st.rerun()
    except TypeError:
        st.dataframe(show, use_container_width=True, hide_index=True)
        selected = st.selectbox("Detayı açılacak şikâyet", df.id.tolist(), format_func=lambda i: f"{df.loc[df.id==i,'complaint_no'].iloc[0]} · {df.loc[df.id==i,'product'].iloc[0]}")
        if st.button("Detay sayfasına git", type="primary"):
            st.session_state.detail_id = int(selected)
            st.session_state.pending_page = "Şikâyet Detayı"
            st.rerun()
            
    st.write("") 
    
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        show.to_excel(writer, index=False, sheet_name='Sikayetler')
        
    st.download_button(
        label="📥 Excel Olarak İndir",
        data=buffer.getvalue(),
        file_name="Kalite360_Sikayet_Listesi.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary"
    )