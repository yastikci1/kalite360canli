from failure_classifier import (classify_failure, annotate_failures, resolve_smart_root_cause, extract_root_cause, FAILURE_GROUPS, ALL_SUBTYPES, SUBTYPE_TO_GROUP, UNKNOWN_SUBTYPE, filter_failures, detect_failure_filter, legacy_failure_group)
import hashlib
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
    
    # Model seçimi yalnızca görünümü filtreler; veri silmez.

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
    df = annotate_failures(df)
    df["Hata Nedeni"] = df["Alt Arıza Türü"]
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

    if product_df.empty:
        return st.info("Seçilen model ve zaman aralığında kayıt bulunmuyor.")
    st.caption("Arıza türleri; şikâyet metni ve üretici raporundaki bulgulardan sınıflandırılır. Kesin kök neden tespiti ayrıca değerlendirilir.")
    f_group, f_level, f_scope = st.columns([2, 2, 2])
    group_options = ["Tüm arıza grupları"] + list(FAILURE_GROUPS)
    selected_group = f_group.selectbox("Ana arıza grubu", group_options, key="failure_group_filter")
    detail_level = f_level.selectbox("Dağılım düzeyi", ["Alt arıza türü", "Ana arıza grubu"], key="failure_level")
    scope_mode = f_scope.selectbox("Sayım kapsamı", ["Birincil arıza", "Tüm bulgular"], key="failure_scope")
    def distribution_labels(row):
        labels = row["_failure_labels"] if scope_mode == "Tüm bulgular" else (row["Alt Arıza Türü"],)
        if selected_group != "Tüm arıza grupları":
            labels = tuple(x for x in labels if SUBTYPE_TO_GROUP[x] == selected_group)
        if detail_level == "Ana arıza grubu":
            labels = tuple(SUBTYPE_TO_GROUP[x] for x in labels)
        return tuple(dict.fromkeys(labels))
    product_df = product_df.copy()
    product_df["_distribution_labels"] = product_df.apply(distribution_labels, axis=1)
    product_df = product_df[product_df["_distribution_labels"].map(bool)]
    chart_context = "|".join([selected_product, selected_time, selected_group, detail_level, scope_mode])
    chart_key = "reason_chart_" + hashlib.sha256(chart_context.encode()).hexdigest()[:12]
    selected_reason = None
    chart_state = st.session_state.get(chart_key)
    if chart_state:
        points = chart_state.get("selection", {}).get("points", [])
        if points:
            selected_reason = points[0].get("y")
            if not product_df["_distribution_labels"].map(lambda labels: selected_reason in labels).any():
                selected_reason = None

    if selected_reason:
        kpi_df = product_df[product_df["_distribution_labels"].map(lambda labels: selected_reason in labels)]
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
        st.markdown(f"##### Arıza Dağılımı — {detail_level.title()}")
        st.caption("Detayını görmek istediğiniz arızanın çubuğuna tıklayın.")
        if scope_mode == "Tüm bulgular":
            st.caption(f"{len(product_df)} tekil vaka. Her bulgu vaka başına bir kez sayılır; aynı vakada birden çok bulgu olabileceği için toplam, vaka sayısını aşabilir. Bildirilen belirtiler de kaynaklarıyla birlikte kapsanır.")
        else:
            st.caption("Her vaka yalnızca birincil alt arıza türünde sayılır. Ek bulgular için 'Tüm bulgular' kapsamını seçebilirsiniz.")
        
        reason_rows = product_df[["id", "_distribution_labels"]].explode("_distribution_labels").dropna()
        reason_rows = reason_rows.drop_duplicates(["id", "_distribution_labels"])
        reason_df = reason_rows.groupby("_distribution_labels").size().reset_index(name="Adet").rename(columns={"_distribution_labels": "Hata Nedeni"}).sort_values(["Adet", "Hata Nedeni"], ascending=[True, False])
        fig_reasons = px.bar(reason_df, x="Adet", y="Hata Nedeni", orientation='h', text="Adet")
        
        if selected_reason:
            colors = ['#3b82f6' if cause == selected_reason else '#e2e8f0' for cause in reason_df['Hata Nedeni']]
            fig_reasons.update_traces(marker_color=colors, textposition="outside")
        else:
            fig_reasons.update_traces(marker_color="#6366f1", textposition="outside")
            
        fig_reasons.update_layout(xaxis_title="", yaxis_title="", margin=dict(l=10, r=10, t=10, b=10), height=max(340, len(reason_df) * 30), clickmode='event+select')
        st.plotly_chart(fig_reasons, use_container_width=True, on_select="rerun", key=chart_key)

    st.divider()

    st.markdown("##### Arıza Pareto Analizi (80/20)")
    st.caption("Seçilen sayım kapsamındaki arızaların kümülatif payı gösterilir. %80 çizgisi referanstır; 80/20 oranının sağlandığı varsayılmaz. Tüm bulgular kapsamında oranlar vaka toplamına değil, bulgu toplamına göredir.")
    
    pareto_data = reason_df.sort_values(["Adet", "Hata Nedeni"], ascending=[False, True]).reset_index(drop=True).copy()
    pareto_data['Kümülatif %'] = (pareto_data['Adet'].cumsum() / pareto_data['Adet'].sum()) * 100

    fig_pareto = go.Figure()
    fig_pareto.add_trace(go.Bar(x=pareto_data['Hata Nedeni'], y=pareto_data['Adet'], name='Vaka Sayısı', marker_color='#cbd5e1', hovertemplate='Kök Neden: %{x}<br>Vaka Sayısı: %{y}<extra></extra>'))
    fig_pareto.add_trace(go.Scatter(x=pareto_data['Hata Nedeni'], y=pareto_data['Kümülatif %'], name='Kümülatif %', yaxis='y2', mode='lines+markers', line=dict(color='#ef4444', width=3), marker=dict(size=8, color='#ef4444', line=dict(width=2, color='white')), hovertemplate='Kümülatif: %{y:.1f}%<extra></extra>'))
    fig_pareto.add_hline(y=80, line_dash="dash", line_color="rgba(239, 68, 68, 0.5)", yref="y2", annotation_text="%80 Kritik Eşik", annotation_position="top left")
    oranlar = pareto_data["Kümülatif %"].tolist()
    i = next(i for i, oran in enumerate(oranlar) if oran >= 80)

    if i == 0:
        x_kesisim = 0
    else:
        x_kesisim = i - 1 + (
            (80 - oranlar[i - 1]) / (oranlar[i] - oranlar[i - 1])
        )

    fig_pareto.add_shape(
        type="line",
        xref="x",
        yref="y2",
        x0=x_kesisim,
        x1=x_kesisim,
        y0=0,
        y1=80,
        line=dict(color="#ef4444", width=2, dash="dash")
    )
    fig_pareto.update_layout(
        height=450, margin=dict(l=10, r=10, t=20, b=10), plot_bgcolor='rgba(0,0,0,0)', hovermode='x unified', showlegend=False,
        yaxis=dict(title='Vaka Adedi', showgrid=False), yaxis2=dict(title='Kümülatif Etki (%)', overlaying='y', side='right', range=[0, 105], showgrid=False)
    )
    
    st.plotly_chart(fig_pareto, use_container_width=True)
