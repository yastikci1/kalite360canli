import streamlit as st
from db import init_db, authenticate

st.set_page_config(page_title="Kalite360 Fabrika", page_icon="K", layout="wide")

init_db()

if "user" not in st.session_state: st.session_state.user = None
if st.session_state.get("pending_page"): st.session_state["page_nav"] = st.session_state.pop("pending_page")
if "page_nav" not in st.session_state: st.session_state["page_nav"] = "Dashboard"

MENU_ICONS = {
    "Dashboard": "Dashboard", 
    "Şikâyet Listesi": "Şikâyet Listesi", 
    "Yeni Şikâyet": "Yeni Şikâyet", 
    "Şikâyet Detayı": "Şikâyet Detayı", 
    "Veri Aktarımı": "Veri Aktarımı", 
    "Yönetim": "Yönetim"
}

if st.session_state.get("page_nav") not in MENU_ICONS:
    st.session_state["page_nav"] = "Dashboard"

# --- CSS VE YAN MENÜ (SIDEBAR) TASARIMI ---
st.markdown("""<style>
    /* 1. Yan Menü (Sidebar) Ana Arka Plan (Beau Mavisi) */
    [data-testid="stSidebar"],
    [data-testid="stSidebar"] > div:first-child,
    [data-testid="stSidebarContent"] {
        background: #BCD4E6 !important;
        border-right: 1px solid #A6C4DB !important;
    }

    [data-testid="stSidebar"] button[kind="header"],
    [data-testid="collapsedControl"] {
        color: #1e293b !important;
    }

    /* 2. Üst Logo Alanı (Kalite360 - Veriden Kaliteye) */
    .kalite360-logo-wrap {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 4px 6px 16px 6px;
        margin-bottom: 4px;
    }
    .kalite360-logo-title {
        font-size: 1.6rem;
        font-weight: 800;
        letter-spacing: -0.5px;
        line-height: 1.1;
        color: #0f172a !important;
        margin: 0;
    }
    .kalite360-logo-title span {
        color: #0284c7 !important;
    }
    .kalite360-logo-sub {
        font-size: 0.80rem;
        color: #334155 !important;
        margin-top: 3px;
        font-weight: 600;
    }

    /* 3. Kullanıcı Bilgi Kartı */
    .sidebar-user-card {
        background: #A6C4DB !important;
        border: 1px solid #8CB4D1 !important;
        border-radius: 12px;
        padding: 12px 14px;
        margin-bottom: 18px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
    }

    /* 4. Menü Sekmeleri ve Aktif Sayfa Göstergesi */
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
        gap: 6px !important;
    }

    /* Seçili Olmayan (Normal) Menü Sekmeleri */
    [data-testid="stSidebar"] button[kind="secondary"] {
        background: transparent !important;
        border: 1px solid transparent !important;
        border-radius: 10px !important;
        padding: 11px 16px !important;
        width: 100% !important;
        display: flex !important;
        justify-content: flex-start !important;
        align-items: center !important;
        text-align: left !important;
        color: #1e293b !important;
        box-shadow: none !important;
        transition: all 0.18s ease-in-out !important;
        margin: 0 !important;
    }
    [data-testid="stSidebar"] button[kind="secondary"] > div {
        display: flex !important;
        justify-content: flex-start !important;
        align-items: center !important;
        width: 100% !important;
    }
    [data-testid="stSidebar"] button[kind="secondary"] p,
    [data-testid="stSidebar"] button[kind="secondary"] span {
        color: #1e293b !important;
        font-size: 15px !important;
        font-weight: 600 !important;
        margin: 0 !important;
        text-align: left !important;
    }

    /* Üzerine Gelindiğinde (Hover) - Biraz daha koyusu */
    [data-testid="stSidebar"] button[kind="secondary"]:hover {
        background: #A6C4DB !important;
        border-color: #92B9D6 !important;
        transform: translateX(4px) !important;
        box-shadow: 0 3px 10px rgba(0, 0, 0, 0.08) !important;
    }
    [data-testid="stSidebar"] button[kind="secondary"]:hover p,
    [data-testid="stSidebar"] button[kind="secondary"]:hover span {
        color: #0f172a !important;
        font-weight: 700 !important;
    }

    /* Seçili (Aktif) Menü Sekmesi - En koyu vurgu */
    [data-testid="stSidebar"] button[kind="primary"] {
        background: #8CB4D1 !important;
        border: 1px solid #729FBF !important;
        border-radius: 10px !important;
        padding: 11px 16px !important;
        width: 100% !important;
        display: flex !important;
        justify-content: flex-start !important;
        align-items: center !important;
        text-align: left !important;
        color: #0f172a !important;
        box-shadow: 0 4px 14px rgba(0, 0, 0, 0.1) !important;
        transition: all 0.18s ease-in-out !important;
        margin: 0 !important;
    }
    [data-testid="stSidebar"] button[kind="primary"] > div {
        display: flex !important;
        justify-content: flex-start !important;
        align-items: center !important;
        width: 100% !important;
    }
    [data-testid="stSidebar"] button[kind="primary"] p,
    [data-testid="stSidebar"] button[kind="primary"] span {
        color: #0f172a !important;
        font-size: 15px !important;
        font-weight: 800 !important;
        margin: 0 !important;
        text-align: left !important;
    }

    /* Boş halka: diğer sayfalar. Dolu mavi halka: seçili sayfa. */
    [data-testid="stSidebar"] button[kind="secondary"],
    [data-testid="stSidebar"] button[kind="primary"] {
        gap: 12px !important;
    }
    [data-testid="stSidebar"] button[kind="secondary"]::before,
    [data-testid="stSidebar"] button[kind="primary"]::before {
        content: "";
        display: block;
        flex: 0 0 10px;
        width: 10px;
        height: 10px;
        box-sizing: border-box;
        border: 2px solid #7399b3;
        border-radius: 50%;
        background: transparent;
    }
    [data-testid="stSidebar"] button[kind="primary"]::before {
        background: #0369a1;
        border-color: #0369a1;
        box-shadow: 0 0 0 4px rgba(3, 105, 161, 0.14);
    }
    [data-testid="stSidebar"] button[kind="secondary"]:hover::before {
        border-color: #0369a1;
    }

    /* 5. Alt Ayırıcı Çizgi ve Çıkış Yap Butonu */
    .sidebar-bottom-divider {
        border-top: 1px solid rgba(0, 0, 0, 0.1);
        margin-top: 22px;
        padding-top: 12px;
    }
    [data-testid="stSidebar"] .st-key-btn_logout_sidebar button {
        border-radius: 10px !important;
        border: 1px solid rgba(239, 68, 68, 0.45) !important;
        background: rgba(239, 68, 68, 0.1) !important;
        color: #b91c1c !important;
        justify-content: center !important;
        text-align: center !important;
        padding: 10px 16px !important;
        margin-top: 4px !important;
        transform: none !important;
    }
    [data-testid="stSidebar"] .st-key-btn_logout_sidebar button > div {
        justify-content: center !important;
    }
    [data-testid="stSidebar"] .st-key-btn_logout_sidebar button::before {
        display: none;
    }
    [data-testid="stSidebar"] .st-key-btn_logout_sidebar button p {
        color: #b91c1c !important;
        font-weight: 700 !important;
        text-align: center !important;
    }
    [data-testid="stSidebar"] .st-key-btn_logout_sidebar button:hover {
        background: #ef4444 !important;
        border-color: #dc2626 !important;
        transform: none !important;
    }
    [data-testid="stSidebar"] .st-key-btn_logout_sidebar button:hover p {
        color: #ffffff !important;
    }
</style>""", unsafe_allow_html=True)

def login():
    st.title("Kalite360 | Fabrika Şikâyet Yönetimi")
    with st.form("login"):
        username = st.text_input("Kullanıcı adı")
        password = st.text_input("Parola", type="password")
        if st.form_submit_button("Giriş yap", type="primary"):
            u = authenticate(username, password)
            if u: 
                st.session_state.user = u
                st.rerun()
            else: st.error("Hatalı giriş.")
    st.caption("Pilot hesaplar: musterihizmetleri / MH2026! · kalite / Kalite2026! · admin / Admin2026!")

if not st.session_state.user:
    login()
    st.stop()

user = st.session_state.user

# Kalite360 Logosu + Kullanıcı Kartı
st.sidebar.markdown(f"""
<div class="kalite360-logo-wrap">
    <svg width="52" height="52" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
        <defs>
            <linearGradient id="k360_grad1" x1="8" y1="8" x2="56" y2="56" gradientUnits="userSpaceOnUse">
                <stop offset="0%" stop-color="#0284c7"/>
                <stop offset="100%" stop-color="#0369a1"/>
            </linearGradient>
            <linearGradient id="k360_grad2" x1="12" y1="48" x2="48" y2="12" gradientUnits="userSpaceOnUse">
                <stop offset="0%" stop-color="#e0f2fe"/>
                <stop offset="100%" stop-color="#0284c7"/>
            </linearGradient>
        </defs>
        <circle cx="30" cy="34" r="21" stroke="url(#k360_grad1)" stroke-width="10" stroke-linecap="round" stroke-dasharray="105 30"/>
        <circle cx="30" cy="34" r="11" fill="#f8fafc" stroke="url(#k360_grad2)" stroke-width="3"/>
        <circle cx="42" cy="22" r="3.5" fill="#0284c7"/>
        <circle cx="50" cy="14" r="4.5" fill="#0ea5e9"/>
        <path d="M35 29L42 22" stroke="#0284c7" stroke-width="2.5" stroke-linecap="round"/>
    </svg>
    <div>
        <div class="kalite360-logo-title">Kalite<span>360</span></div>
        <div class="kalite360-logo-sub">Veriden Kaliteye</div>
    </div>
</div>
<div class="sidebar-user-card">
    <div style="font-size: 1.02rem; font-weight: 800; color: #0f172a; margin-bottom: 3px;"> Kalite360</div>
    <div style="font-size: 0.84rem; color: #1e293b; font-weight: 600;">{user["full_name"]} <span style="color:#0284c7; font-weight: 700;">• {user["role"]}</span></div>
</div>
""", unsafe_allow_html=True)

# Şık Menü Sekmeleri
for menu_key, menu_label in MENU_ICONS.items():
    is_active = (st.session_state["page_nav"] == menu_key)
    if st.sidebar.button(
        menu_label,
        key=f"nav_btn_{menu_key}",
        type="primary" if is_active else "secondary",
        use_container_width=True
    ):
        if not is_active:
            st.session_state["page_nav"] = menu_key
            st.rerun()

page = st.session_state["page_nav"]

st.sidebar.markdown('<div class="sidebar-bottom-divider"></div>', unsafe_allow_html=True)
if st.sidebar.button("Çıkış yap", key="btn_logout_sidebar", use_container_width=True):
    st.session_state.user = None
    st.rerun()

if page != "Şikâyet Detayı":
    st.title(page)

# --- ROUTER (YÖNLENDİRİCİ) ---
if page == "Dashboard":
    import views.dashboard as v_dashboard
    v_dashboard.render()
elif page == "Yeni Şikâyet":
    import views.yeni_sikayet as v_yeni_sikayet
    v_yeni_sikayet.render(user)
elif page == "Şikâyet Listesi":
    import views.sikayet_listesi as v_sikayet_listesi
    v_sikayet_listesi.render()
elif page == "Şikâyet Detayı":
    import views.sikayet_detayi as v_sikayet_detayi
    v_sikayet_detayi.render(user)
elif page == "Veri Aktarımı":
    import views.veri_aktarimi as v_veri_aktarimi
    v_veri_aktarimi.render(user)
elif page == "Yönetim":
    import views.yonetim as v_yonetim
    v_yonetim.render()

# SOHBET ASİSTANI BİLEŞENİ
import ai_chat_widget   
ai_chat_widget.render_chat()
