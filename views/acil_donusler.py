import streamlit as st
from db import complaints

def render():
    df = complaints()
    if df.empty: 
        st.info("Kayıt yok.")
        return
        
    df = df[(df.urgent_return == 1) & (df.status != "Kapalı")]
    if df.empty: 
        st.success("Açık acil geri dönüş yok.")
    else:
        st.error(f"{len(df)} açık acil geri dönüş kaydı")
        st.dataframe(df[["complaint_no", "customer", "phone", "product", "status", "owner", "created_at"]], use_container_width=True, hide_index=True)
        for _, r in df.iterrows():
            if st.button(f"Detayı aç: {r['complaint_no']} · {r['customer']}", key=f"urgent_{r['id']}"):
                st.session_state.detail_id = int(r["id"])
                st.session_state.pending_page = "Şikâyet Detayı"
                st.rerun()