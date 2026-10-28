import streamlit as st
from db import CATEGORIES, create_complaint
from services.ai_service import analyze_complaint_text
from utils import notify_new_complaint

def render(user):
    st.caption("Şikâyeti formdan girebilir veya serbest metni yapıştırarak analiz ettirebilirsiniz.")
    mode = st.radio("Yöntem", ["Formu kendim dolduracağım", "E-posta / şikâyet metnini yapıştır"], horizontal=True)
    
    if mode == "E-posta / şikâyet metnini yapıştır":
        raw = st.text_area("Şikâyet metni", height=180)
        if st.button("Metni analiz et", type="primary", key="btn_parse_text"):
            if raw.strip():
                try:
                    st.session_state.ai_draft = analyze_complaint_text(raw)
                    st.session_state.ai_raw = raw
                    st.success("Metin analiz edildi.")
                except Exception as e:
                    st.error(f"Hata: {e}")
        
        draft = st.session_state.get("ai_draft")
        if draft:
            with st.form("ai_draft_form"):
                a, b = st.columns(2)
                customer = a.text_input("Müşteri *", draft.get("customer", ""))
                contact = b.text_input("İletişim", draft.get("contact", ""))
                c, d = st.columns(2)
                phone = c.text_input("Telefon", draft.get("phone", ""))
                email = d.text_input("E-posta", draft.get("email", ""))
                e, f = st.columns(2)
                product = e.text_input("Ürün *", draft.get("product", ""))
                order = f.text_input("Sipariş No", draft.get("order", ""))
                g, h = st.columns(2)
                category = g.selectbox("Sınıf", CATEGORIES, index=CATEGORIES.index(draft.get("category", "Diğer")) if draft.get("category") in CATEGORIES else 0)
                priority = h.selectbox("Öncelik", ["Düşük", "Orta", "Yüksek", "Kritik"], index=1)
                description = st.text_area("Açıklama *", draft.get("description", ""))
                requested = st.text_area("Talep", draft.get("requested", ""))
                urgent = st.checkbox("Acil dönüş gerekli", draft.get("urgent", False))
                owner = st.text_input("Sorumlu", value="kalite")
                save = st.form_submit_button("Şikâyeti Kaydet", type="primary")
            if save:
                if not customer.strip() or not product.strip() or not description.strip(): 
                    st.error("Müşteri, ürün ve açıklama zorunludur.")
                else:
                    cid = create_complaint(customer, contact, phone, email, product, order, category, priority, description, requested, owner, user["username"], urgent)
                    notify_new_complaint(cid)
                    st.session_state.detail_id = cid
                    st.session_state.pending_page = "Şikâyet Detayı"
                    st.rerun()
    else:
        with st.form("new", clear_on_submit=True):
            a, b = st.columns(2)
            customer = a.text_input("Müşteri / Kurum *")
            contact = b.text_input("İletişim kişisi")
            c, d = st.columns(2)
            phone = c.text_input("Telefon")
            email = d.text_input("E-posta")
            e, f = st.columns(2)
            product = e.text_input("Ürün *")
            order = f.text_input("Sipariş/MDR No")
            g, h = st.columns(2)
            category = g.selectbox("Sınıf", CATEGORIES)
            priority = h.selectbox("Öncelik", ["Düşük", "Orta", "Yüksek", "Kritik"], index=1)
            description = st.text_area("Açıklama *")
            requested = st.text_area("Talep")
            urgent = st.checkbox("Acil geri dönüş gerekli")
            owner = st.text_input("Sorumlu", value="kalite")
            if st.form_submit_button("Kaydet", type="primary"):
                if not customer.strip() or not product.strip() or not description.strip(): 
                    st.error("Zorunlu alanları doldurun.")
                else:
                    cid = create_complaint(customer, contact, phone, email, product, order, category, priority, description, requested, owner, user["username"], urgent)
                    notify_new_complaint(cid)
                    st.session_state.detail_id = cid
                    st.session_state.pending_page = "Şikâyet Detayı"
                    st.rerun()