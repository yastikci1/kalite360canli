import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import re
import os
from pathlib import Path
from datetime import datetime, timedelta
from db import complaints, db
from utils import classify_validity
from services.mail_service import load_local_env

DEFAULT_MODEL_NUMBERS = "EM800, MR8-AF02"

def get_saved_models():
    raw = DEFAULT_MODEL_NUMBERS
    try:
        from services.mail_service import ENV_PATH
        svc_env = ENV_PATH
    except ImportError:
        svc_env = Path(__file__).resolve().parent.parent / "services" / ".env"

    paths_to_check = [
        Path(__file__).resolve().parent.parent / ".env", 
        svc_env
    ]
    
    found = False
    for path in paths_to_check:
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("SAVED_FDA_MODEL_NUMBERS="):
                    raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                    found = True
                    break
        if found:
            break
            
    models = [c.strip().upper() for c in raw.split(",") if c.strip()]
    return models if models else ["EM800", "MR8-AF02"]

def purge_unlisted_products(saved_models):
    """Kayıtlı model numaraları (örn. EM800, MR8-AF02) dışında kalan tüm ürünleri veritabanından temizler."""
    if not saved_models:
        return
    conditions = []
    params = []
    for m in saved_models:
        conditions.append("(UPPER(product) LIKE ? OR UPPER(findings) LIKE ? OR UPPER(description) LIKE ?)")
        params.extend([f"%{m}%", f"%{m}%", f"%{m}%"])
    where_clause = " OR ".join(conditions)
    with db() as conn:
        conn.execute(f"DELETE FROM complaints WHERE NOT ({where_clause})", params)
        conn.commit()

def _clean_negations(text: str) -> str:
    """Servis raporlarındaki 'no defect', 'no overheating' gibi negatif test ifadelerini temizler."""
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
    """Metin içerisinden genişletilmiş medikal ve endüstriyel anahtar kelimelerle kök neden çıkarır."""
    t = str(text or "").lower().strip()
    if not t or "fda maude kaydında olay metni belirtilmemiş" in t:
        return "Diğer / Tanımlanamayan"

    # 1. Aşırı Isınma / Termal Sapma
    if any(k in t for k in [
        "overheat", "over-heat", "hot", "extremely warm", "warm to touch", "thermal",
        "temperature", "burn", "burned", "burnt", "scald", "heat", "cooling",
        "ısınma", "ısındı", "sıcaklık", "sıcak", "termal", "yanık", "aşırı ısı"
    ]):
        return "Aşırı Isınma"

    # 2. Fiziksel Kırılma / Parça Kopması / Çatlak
    if any(k in t for k in [
        "break", "broke", "broken", "crack", "cracked", "fracture", "fractured",
        "snap", "snapped", "shatter", "split", "severed", "sheared", "chipped",
        "detach", "detached", "detachment", "separat", "came apart", "fell apart",
        "fell off", "dislodg", "fragment", "piece broke", "bent", "deform", "dent",
        "tip broke", "bur broke", "burr broke", "flute broke", "leg broke", "footplate broke",
        "kırık", "kırıl", "çatlak", "çatla", "koptu", "kopma", "ayrıl", "parçalan", "yamul", "ezil"
    ]):
        return "Fiziksel Kırılma / Çatlak"

    # 3. Rulman / Mekanik Aşınma & Bakım Eksikliği
    if any(k in t for k in [
        "bearing", "worn", "wear", "rulman", "bilya", "aşınma", "aşındı", "dağılma",
        "preventive maintenance", "inadequate maintenance", "lack of maintenance",
        "lubricat", "grease", "oiling", "friction", "spindle", "rotor", "shaft worn",
        "vane", "gear worn", "scoring", "galling", "rubbing", "fatigue", "overhaul",
        "bakım eksik", "yağlama", "mekanik yıpranma", "mil aşın"
    ]):
        return "Rulman / Mekanik Aşınma"

    # 4. Mekanik Sıkışma / Pens / Uç Kilitlenmesi
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

    # 5. Anormal Ses / Salgı / Titreşim
    if any(k in t for k in [
        "noise", "noisy", "ses", "rough", "vibrat", "titreşim", "vibrasyon",
        "wobbl", "runout", "run-out", "eccentric", "salgı", "yalpalama",
        "chatter", "rattl", "grind", "squeal", "whine", "screech", "loud",
        "audible", "unbalanced", "unstable", "jumping", "skipping"
    ]):
        return "Anormal Ses / Titreşim"

    # 6. Kablo / Hortum / Bağlantı Temassızlığı
    if any(k in t for k in [
        "cable", "wire", "wiring", "cord", "kablo", "temassız", "connector",
        "plug", "pin", "socket", "receptacle", "hose", "tube", "tubing",
        "pneumatic", "air leak", "hava kaçağı", "frayed", "exposed wire",
        "strain relief", "kink", "pedal", "footswitch", "footpedal", "handpiece cord",
        "konnektör", "soket", "bağlantı kopuk"
    ]):
        return "Kablo / Temassızlık"

    # 7. Elektriksel Hata / Kaçak / Elektronik Arıza
    if any(k in t for k in [
        "hipot", "hi-pot", "shock", "electrical", "electric", "kaçak", "elektrik",
        "short circuit", "shorted", "open circuit", "current", "voltage", "ground",
        "insulation", "dielectric", "spark", "arc", "arcing", "smoke", "smell", "odor",
        "fuse", "board", "pcb", "sensor", "hall", "stator", "coil", "winding",
        "resistance", "ohm", "error code", "fault code", "alarm", "console error",
        "kısa devre", "izolasyon", "sensör", "stator", "bobin", "hata kodu"
    ]):
        return "Elektriksel Hata / Kaçak"

    # 8. Korozyon / Oksitlenme / Sızdırmazlık
    if any(k in t for k in [
        "corrosion", "corroded", "corros", "oksit", "pas", "paslanma", "rust", "rusted",
        "oxid", "pitting", "pitted", "stain", "discolor", "faded", "peel", "peeling",
        "flak", "coating", "anodiz", "autoclave", "steriliz", "washer", "chemical",
        "moisture", "water ingress", "fluid ingress", "seal", "o-ring", "gasket",
        "leak", "leaking", "sızdırma", "korozyon", "sterilizasyon"
    ]):
        return "Korozyon / Oksitlenme"

    # 9. Ani Durma / Çalışmama / Fonksiyon & Güç Kaybı
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
    """
    3 Aşamalı Akıllı Kök Neden Çözümleyici:
    1) Önce müşteri şikâyet metnini (description + tr_description) analiz eder.
    2) Bulunamazsa üretici servis raporunu (action_plan + tr_action_plan + root_cause) negatif ifadeleri temizleyerek analiz eder.
    3) Hâlâ bulunamazsa parça/bağlam ipuçlarından (bur/attachment/motor/service) akıllı çıkarım yapar.
    """
    # Aşama 1: Müşteri Şikâyeti
    desc_combined = f"{row.get('description') or ''} {row.get('tr_description') or ''}"
    cause = extract_root_cause(desc_combined)
    if cause != "Diğer / Tanımlanamayan":
        return cause

    # Aşama 2: Üretici Teknik Servis Raporu & Kök Neden Sütunu
    rc_raw = f"{row.get('root_cause') or ''} {row.get('action_plan') or ''} {row.get('tr_action_plan') or ''}"
    rc_cleaned = _clean_negations(rc_raw)
    cause = extract_root_cause(rc_cleaned)
    if cause != "Diğer / Tanımlanamayan":
        return cause

    # Aşama 3: Bağlamsal Yedek Çıkarım (Contextual Fallback)
    full_context = f"{desc_combined} {rc_cleaned} {row.get('findings') or ''}".lower()
    if any(k in full_context for k in ["bur", "burr", "attachment", "footplate", "guard", "hood", "collet", "tube", "chuck", "sleeve"]):
        return "Mekanik Sıkışma / Kilitlenme"
    if any(k in full_context for k in ["service", "repaired", "replaced", "overhaul", "rebuild", "maintenance", "worn", "age", "wear"]):
        return "Rulman / Mekanik Aşınma"
    if any(k in full_context for k in ["damage", "damaged", "broken", "fracture", "bent", "dent", "missing", "tip"]):
        return "Fiziksel Kırılma / Çatlak"
    if any(k in full_context for k in ["malfunction", "injury", "test", "evaluated", "evaluation", "handpiece", "motor", "drill"]):
        return "Ani Durma / Çalışmama"

    return "Diğer / Tanımlanamayan"

def resolve_model_group(row, saved_models):
    prod = str(row.get("product") or "").upper()
    findings = str(row.get("findings") or "").upper()
    desc = str(row.get("description") or "").upper()
    combined = f"{prod} {findings} {desc}"

    for m in sorted(saved_models, key=len, reverse=True):
        if m and m in combined:
            return m
    return "Diğer"

def render():
    st.markdown("""
        <style>
        .kpi-card { background-color: var(--secondary-background-color); border: 1px solid rgba(128, 128, 128, 0.2); border-radius: 12px; padding: 20px; text-align: center; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05); height: 100%; display: flex; flex-direction: column; justify-content: center; }
        .kpi-title { font-size: 13px; font-weight: 600; color: gray; margin-bottom: 8px; text-transform: uppercase; }
        .kpi-value { font-size: 26px; font-weight: 700; color: var(--text-color); margin-bottom: 4px; }
        .kpi-sub { font-size: 13px; font-weight: 500; }
        .text-green { color: #10b981; }
        .text-red { color: #ef4444; }
        .text-blue { color: #3b82f6; }
        .text-orange { color: #f59e0b; }
        </style>
    """, unsafe_allow_html=True)

    saved_models = get_saved_models()
    
    # Kayıtlı modeller dışındaki tüm ürünleri veritabanından temizle
    purge_unlisted_products(saved_models)

    df = complaints()
    
    # Sadece kayıtlı Model Number'lar açılır listede yer alır
    products = ["Tüm Ürünler"] + saved_models

    time_filters = {
        "Tüm Zamanlar": None,
        "1 Yıl": 365,
        "9 Ay": 270,
        "6 Ay": 180,
        "3 Ay": 90,
        "15 Günlük": 15
    }
    
    col_filter, col_time, _ = st.columns([1, 1, 1])
    with col_filter:
        selected_product = st.selectbox("🎯 Analiz Edilecek Ürün / Model Number:", products)
    with col_time:
        selected_time = st.selectbox("📅 Zaman Filtresi:", list(time_filters.keys()))

    if df.empty:
        return st.info("Sistemde henüz analiz edilecek bir şikâyet verisi bulunmuyor. Veri Aktarımı sayfasından modelleri güncelleyebilirsiniz.")

    df["Şikâyet Doğruluğu"] = df.apply(classify_validity, axis=1)
    df["Hata Nedeni"] = df.apply(resolve_smart_root_cause, axis=1)
    df["Ürün Grubu"] = df.apply(lambda r: resolve_model_group(r, saved_models), axis=1)

    # Sadece kayıtlı modellere ait satırları tut
    df = df[df["Ürün Grubu"].isin(saved_models)]

    # Zaman filtresini uygula
    df["_dt"] = pd.to_datetime(df["created_at"], format="mixed", errors="coerce")
    days_limit = time_filters.get(selected_time)
    if days_limit is not None:
        max_dt = df["_dt"].max()
        ref_date = max_dt if pd.notna(max_dt) else datetime.now()
        df = df[df["_dt"] >= (ref_date - timedelta(days=days_limit))]

    if selected_product != "Tüm Ürünler":
        product_df = df[df["Ürün Grubu"] == selected_product]
        raw_prod = str(product_df.iloc[0]["product"]) if not product_df.empty else ""
        mfr_match = re.search(r'\[(.*?)\]', raw_prod)
        manufacturer = mfr_match.group(1).strip().title() if mfr_match else (raw_prod.split()[0].title() if raw_prod else "Bilinmiyor")
    else:
        product_df = df
        manufacturer = "Çoklu Üretici / Tüm Tesis"

    selected_reason = None
    chart_state = st.session_state.get("reason_chart_select")
    if chart_state and isinstance(chart_state, dict):
        points = chart_state.get("selection", {}).get("points", [])
        if points:
            selected_reason = points[0].get("y")
            if selected_reason not in product_df["Hata Nedeni"].values:
                selected_reason = None

    if selected_reason:
        kpi_df = product_df[product_df["Hata Nedeni"] == selected_reason]
        st.info(f"🔍 **Dinamik Filtre Devrede:** Sadece **{selected_reason}** vakaları analiz ediliyor. Tümünü görmek için grafikte boş bir yere tıklayın.")
        kpi_subtitle = f"Filtre: {selected_reason}"
    else:
        kpi_df = product_df
        kpi_subtitle = "Tüm arıza türleri"

    total_complaints = len(kpi_df)
    dogru_count = len(kpi_df[kpi_df["Şikâyet Doğruluğu"] == "Şikâyet Doğru"])
    yanlis_count = len(kpi_df[kpi_df["Şikâyet Doğruluğu"] == "Şikâyet Yanlış"])
    bekleyen_count = total_complaints - (dogru_count + yanlis_count)
    
    dogru_pct = (dogru_count / total_complaints * 100) if total_complaints > 0 else 0
    yanlis_pct = (yanlis_count / total_complaints * 100) if total_complaints > 0 else 0
    bekleyen_pct = (bekleyen_count / total_complaints * 100) if total_complaints > 0 else 0

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.markdown(f'<div class="kpi-card"><div class="kpi-title">Üretici / Marka</div><div class="kpi-value" style="font-size: 18px; margin-top: 10px;">{manufacturer}</div></div>', unsafe_allow_html=True)
    k2.markdown(f'<div class="kpi-card"><div class="kpi-title">Toplam Vaka</div><div class="kpi-value text-blue">{total_complaints}</div><div class="kpi-sub">{kpi_subtitle}</div></div>', unsafe_allow_html=True)
    k3.markdown(f'<div class="kpi-card"><div class="kpi-title">İnceleme Bekleyen</div><div class="kpi-value text-orange">{bekleyen_count}</div><div class="kpi-sub text-orange">% {bekleyen_pct:.1f} İşlemde</div></div>', unsafe_allow_html=True)
    k4.markdown(f'<div class="kpi-card"><div class="kpi-title">Doğrulanan Arıza</div><div class="kpi-value text-red">{dogru_count}</div><div class="kpi-sub text-red">% {dogru_pct:.1f} Üretici Onaylı</div></div>', unsafe_allow_html=True)
    k5.markdown(f'<div class="kpi-card"><div class="kpi-title">Yersiz / Hata Yok</div><div class="kpi-value text-green">{yanlis_count}</div><div class="kpi-sub text-green">% {yanlis_pct:.1f} Sorunsuz</div></div>', unsafe_allow_html=True)

    st.write("")
    st.write("")

    if len(product_df) == 0:
        return st.warning(f"**{selected_product}** model numarası ({selected_time}) için henüz sistemde kayıtlı veri bulunamadı. Veri Aktarımı sayfasından güncelleyebilirsiniz.")

    c1, c2 = st.columns(2)
    
    with c1:
        st.markdown(f"##### 📈 Aylık Şikâyet Trendi {f'({selected_reason})' if selected_reason else ''}")
        trend_df = kpi_df.copy()
        if not trend_df.empty:
            trend_df["Ay"] = pd.to_datetime(trend_df["created_at"], format="mixed", errors="coerce").dt.to_period("M").astype(str)
            trend_df = trend_df[trend_df["Ay"] != "NaT"]
            trend_grouped = trend_df.groupby("Ay").size().reset_index(name="Adet")
            fig_trend = px.line(trend_grouped, x="Ay", y="Adet", markers=True, line_shape="spline", color_discrete_sequence=["#3b82f6"])
            fig_trend.update_layout(xaxis_title="", yaxis_title="Vaka Sayısı", margin=dict(l=10, r=10, t=10, b=10), height=320)
            st.plotly_chart(fig_trend, use_container_width=True)
        else:
            st.info("Trend çizecek yeterli veri yok.")
        
    with c2:
        st.markdown("##### 🧩 Şikâyet / Hata Nedenleri Dağılımı")
        st.caption("💡 Etkileşimli: Detayını görmek istediğiniz arızanın çubuğuna tıklayın.")
        
        reason_df = product_df.groupby("Hata Nedeni").size().reset_index(name="Adet").sort_values("Adet", ascending=True)
        fig_reasons = px.bar(reason_df, x="Adet", y="Hata Nedeni", orientation='h', text="Adet")
        
        if selected_reason:
            colors = ['#3b82f6' if cause == selected_reason else '#e2e8f0' for cause in reason_df['Hata Nedeni']]
            fig_reasons.update_traces(marker_color=colors, textposition="outside")
        else:
            fig_reasons.update_traces(marker_color="#6366f1", textposition="outside")
            
        fig_reasons.update_layout(xaxis_title="", yaxis_title="", margin=dict(l=10, r=10, t=10, b=10), height=320, clickmode='event+select')
        st.plotly_chart(fig_reasons, use_container_width=True, on_select="rerun", key="reason_chart_select")

    st.divider()

    st.markdown("##### 📊 Kök Neden Pareto Analizi (80/20 Kuralı)")
    st.caption("Kalite problemlerinin %80'inin, nedenlerin %20'sinden kaynaklandığını tespit eden endüstriyel Pareto grafiği.")
    
    pareto_data = product_df['Hata Nedeni'].value_counts().reset_index()
    pareto_data.columns = ['Hata Nedeni', 'Adet']
    pareto_data['Kümülatif %'] = (pareto_data['Adet'].cumsum() / pareto_data['Adet'].sum()) * 100

    fig_pareto = go.Figure()
    fig_pareto.add_trace(go.Bar(x=pareto_data['Hata Nedeni'], y=pareto_data['Adet'], name='Vaka Sayısı', marker_color='#cbd5e1', hovertemplate='Kök Neden: %{x}<br>Vaka Sayısı: %{y}<extra></extra>'))
    fig_pareto.add_trace(go.Scatter(x=pareto_data['Hata Nedeni'], y=pareto_data['Kümülatif %'], name='Kümülatif %', yaxis='y2', mode='lines+markers', line=dict(color='#ef4444', width=3), marker=dict(size=8, color='#ef4444', line=dict(width=2, color='white')), hovertemplate='Kümülatif: %{y:.1f}%<extra></extra>'))
    fig_pareto.add_hline(y=80, line_dash="dash", line_color="rgba(239, 68, 68, 0.5)", yref="y2", annotation_text="%80 Kritik Eşik", annotation_position="top left")

    fig_pareto.update_layout(
        height=450, margin=dict(l=10, r=10, t=20, b=10), plot_bgcolor='rgba(0,0,0,0)', hovermode='x unified', showlegend=False,
        yaxis=dict(title='Vaka Adedi', showgrid=False), yaxis2=dict(title='Kümülatif Etki (%)', overlaying='y', side='right', range=[0, 105], showgrid=False)
    )
    
    st.plotly_chart(fig_pareto, use_container_width=True)