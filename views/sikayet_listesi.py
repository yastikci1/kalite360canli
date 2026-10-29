from failure_classifier import (classify_failure, annotate_failures, resolve_smart_root_cause, extract_root_cause, FAILURE_GROUPS, ALL_SUBTYPES, SUBTYPE_TO_GROUP, UNKNOWN_SUBTYPE, filter_failures, detect_failure_filter, legacy_failure_group)
import io
import streamlit as st
import pandas as pd
import re
from db import complaints
from utils import classify_validity
from services.ai_service import is_translation_enabled, ensure_translation_columns
from ai_quality_engine import extract_failure_mode, determine_complaint_validity




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
    df = annotate_failures(df)
    df["Hata Kategorisi"] = df["Alt Arıza Türü"]
    df["Model Grubu"] = df["product"].apply(extract_model_group)
    
    c1, c2, c3, c4 = st.columns([1.5, 1.5, 1, 1])
    
    q = c1.text_input("Ara", placeholder="No veya açıklama ara...")
    
    urun_listesi = ["Tümü"] + sorted(df["Model Grubu"].dropna().unique().tolist())
    prod_f = c2.selectbox("Ürün Modeli", urun_listesi)
    
    dogruluk_listesi = ["Tümü", "Şikâyet Doğru", "Şikâyet Yanlış", "İnceleme Yapılıyor", "Çözüm Belirtilmemiş"]
    val_f = c3.selectbox("Şikâyet Doğruluğu", dogruluk_listesi)
    
    group_f = c4.selectbox("Ana arıza grubu", ["Tümü"] + list(FAILURE_GROUPS))
    relevant_subtypes = [x for x in ALL_SUBTYPES if group_f == "Tümü" or SUBTYPE_TO_GROUP[x] == group_f]
    cause_f = st.selectbox("Alt arıza türü", ["Tümü"] + relevant_subtypes)
    st.caption("Arıza filtreleri birincil ve ek bulgularda arama yapar. Her şikâyet listede bir kez görünür.")
    
    if q: 
        df = df[df.astype(str).apply(lambda col: col.str.contains(q, case=False, na=False)).any(axis=1)]
    if prod_f != "Tümü": 
        df = df[df["Model Grubu"] == prod_f]
    if val_f != "Tümü": 
        df = df[df["Şikâyet Doğruluğu"] == val_f]
    if group_f != "Tümü":
        df = filter_failures(df, group_f)
    if cause_f != "Tümü":
        df = filter_failures(df, cause_f)
        
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

    display_cols = ["ID", "Şikâyet No", "Kayıt Tarihi", "Ürün", "Arıza Grubu", "Alt Arıza Türü", "Ek Bulgular", "Sınıflandırma Kaynağı", "Sınıflandırma Kanıtı", "Kök Neden Durumu", "Şikâyet Doğruluğu", "Müşteri Şikâyeti", "Üretilen Çözüm"]
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