import streamlit as st
from db import init_db, authenticate

st.set_page_config(page_title="Kalite360 Fabrika", page_icon="🏭", layout="wide")

init_db()

if "user" not in st.session_state: st.session_state.user = None
if st.session_state.get("pending_page"): st.session_state["page_nav"] = st.session_state.pop("pending_page")
if "page_nav" not in st.session_state: st.session_state["page_nav"] = "Dashboard"

MENU_ICONS = {
    "Dashboard": "📊  Dashboard", 
    "Şikâyet Listesi": "📋  Şikâyet Listesi", 
    "Yeni Şikâyet": "➕  Yeni Şikâyet", 
    "Şikâyet Detayı": "🔍  Şikâyet Detayı", 
    "Veri Aktarımı": "📥  Veri Aktarımı", 
    "Yönetim": "⚙️  Yönetim"
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

    /* 4. Şık Menü Sekme Butonları (Yuvarlak Noktasız, Sola Hizalı, Net Yazılı) */
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
    """Kalite360 görsel giriş sayfası. Kimlik doğrulama mantığı değişmez."""
    import base64
    from pathlib import Path
    from textwrap import dedent

    factory_path = Path(__file__).resolve().parent / "assets" / "kalite360_factory.svg"
    if factory_path.is_file():
        factory_data = base64.b64encode(factory_path.read_bytes()).decode("ascii")
        factory_url = "data:image/svg+xml;base64," + factory_data
    else:
        factory_url = "none"  # Görsel kopyalanmazsa mavi geçişli arka plan kullanılır.

    user_svg = (
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
        "viewBox='0 0 24 24' fill='none' stroke='%231a4071' stroke-width='1.8' "
        "stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath "
        "d='M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2'/%3E"
        "%3Ccircle cx='12' cy='7' r='4'/%3E%3C/svg%3E"
    )
    lock_svg = (
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
        "viewBox='0 0 24 24' fill='none' stroke='%231a4071' stroke-width='1.8' "
        "stroke-linecap='round' stroke-linejoin='round'%3E"
        "%3Crect x='5' y='10' width='14' height='11' rx='2'/%3E"
        "%3Cpath d='M8 10V7a4 4 0 0 1 8 0v3'/%3E"
        "%3Ccircle cx='12' cy='15' r='1'/%3E%3C/svg%3E"
    )

    css = """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
      #MainMenu, footer, header {visibility:hidden !important;}
      [data-testid="stSidebar"], [data-testid="collapsedControl"] {display:none !important;}
      [data-testid="stAppViewContainer"] {
        font-family:'Inter',system-ui,sans-serif !important;
        background:
          radial-gradient(circle at 90% 90%, #e3edff 0, transparent 42%),
          radial-gradient(circle at 0% 0%, #e9f5ff 0, transparent 40%),
          #edf3fa !important;
      }
      [data-testid="stMainBlockContainer"],
      [data-testid="stAppViewContainer"] .main .block-container {
        max-width: 1480px !important;
        padding: clamp(18px,4.3vh,50px) clamp(12px,3vw,48px) 28px !important;
      }
      div[data-testid="stHorizontalBlock"]:has(.st-key-login_left) {
        gap:0 !important;
        align-items:stretch !important;
        filter:drop-shadow(0 18px 30px rgba(20,65,112,.13));
      }
      div[data-testid="stHorizontalBlock"]:has(.st-key-login_left) > div[data-testid="column"] {
        min-width:0;
      }
      .st-key-login_left, .st-key-login_right {
        min-height:710px;
        box-sizing:border-box;
        overflow:hidden;
        position:relative;
      }

      /* SOL: fabrika illüstrasyonu, büyük mavi eğri ve tanıtım içeriği */
      .st-key-login_left {
        isolation:isolate;
        padding:44px clamp(28px,3.6vw,60px) 28px !important;
        border-radius:23px 0 0 23px;
        background:linear-gradient(142deg,#d4e4f5 0%,#0b5d9b 49%,#003c7b 100%);
      }
      .st-key-login_left::before {
        content:"";
        position:absolute;
        inset:0;
        z-index:-2;
        background-image:
            linear-gradient(145deg,rgba(200,223,245,.85) 0%,rgba(7,76,140,.26) 28%,rgba(3,55,110,.90) 100%),
            url('__FACTORY_URL__');
        background-position:center,center;
        background-size:cover,cover;
      }
      .st-key-login_left::after {
        content:"";
        position:absolute;
        width:980px;
        height:950px;
        left:-490px;
        bottom:-760px;
        background:radial-gradient(ellipse at 54% 44%,rgba(0,145,241,.77),rgba(2,78,148,.15) 68%,transparent 74%);
        border-radius:50%;
        pointer-events:none;
        z-index:-1;
      }
      .st-key-login_left > div {height:100%;}
      .k360-left-layout {display:flex;flex-direction:column;min-height:623px;height:100%;}
      .k360-brand {display:flex;align-items:center;gap:16px;}
      .k360-mark {width:75px;height:75px;flex:0 0 75px;}
      .k360-brand-name {font-size:34px;line-height:1.05;letter-spacing:-1.2px;color:#0e2b55;font-weight:850;}
      .k360-brand-name span {color:#009ddd;}
      .k360-brand-tag {color:#244b77;font-size:15px;margin-top:8px;font-weight:600;}
      .k360-left-copy {margin-top:auto;margin-bottom:auto;padding-top:30px;}
      .k360-eyebrow {width:48px;height:5px;border-radius:10px;background:#74d9ff;margin-bottom:30px;box-shadow:0 0 15px rgba(119,220,255,.65);}
      .k360-headline {font-size:clamp(29px,2.75vw,44px);letter-spacing:-1.15px;line-height:1.20;color:#fff;font-weight:520;margin:0 0 22px;max-width:590px;}
      .k360-headline strong {color:#76d6ff;font-weight:850;display:block;}
      .k360-description {font-size:16px;line-height:1.55;color:#eef7ff;max-width:510px;margin:0 0 39px;}
      .k360-features {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));max-width:490px;gap:6px;}
      .k360-feature {display:flex;flex-direction:column;align-items:center;gap:9px;text-align:center;color:#f3f9ff;font-size:13px;font-weight:600;position:relative;}
      .k360-feature:not(:last-child)::after {content:"";position:absolute;right:-3px;bottom:0;height:36px;border-right:1px solid rgba(115,196,252,.25);}
      .k360-feature-icon {width:64px;height:64px;display:flex;align-items:center;justify-content:center;background:rgba(5,90,162,.28);border:1px solid rgba(121,207,255,.35);border-radius:16px;box-shadow:inset 0 1px 5px rgba(255,255,255,.08);}
      .k360-feature-icon svg {width:31px;height:31px;color:#e8f8ff;stroke:currentColor;}
      .k360-left-footer {font-size:11px;color:#c8e6fa;margin-top:20px;}

      /* SAĞ: pastel dalgalı yüzey ve ortalanmış cam-beyaz giriş kartı */
      .st-key-login_right {
        display:flex;align-items:center;justify-content:center;
        padding:40px clamp(18px,3vw,58px) !important;
        border-radius:0 23px 23px 0;
        background:linear-gradient(150deg,#e5efff 0%,#f5f9ff 47%,#e9f2ff 100%);
        isolation:isolate;
      }
      .st-key-login_right::before {
        content:"";position:absolute;z-index:-1;inset:-40% -26% auto auto;
        width:580px;height:600px;border-radius:48% 52% 60% 40%;
        transform:rotate(27deg);
        background:linear-gradient(105deg,rgba(185,212,249,.52),rgba(255,255,255,0));
      }
      .st-key-login_right::after {
        content:"";position:absolute;z-index:-1;right:-210px;bottom:-470px;
        width:890px;height:720px;border-radius:52%;
        border:55px solid rgba(197,221,255,.26);
        transform:rotate(-27deg);
      }
      .st-key-login_right > div {width:100%;}
      .st-key-login_form_card {
        position:relative;
        border:1px solid #d8e6fa;
        border-radius:20px;
        padding:35px clamp(20px,2.4vw,36px) 22px !important;
        background:rgba(255,255,255,.95) !important;
        box-shadow:0 18px 35px rgba(34,82,148,.12),0 2px 8px rgba(49,87,134,.05);
      }
      .k360-form-brand {display:flex;align-items:center;justify-content:center;gap:15px;margin-bottom:18px;}
      .k360-form-brand .k360-mark {width:65px;height:65px;flex-basis:65px;}
      .k360-form-brand .k360-brand-name {font-size:35px;}
      .k360-form-brand .k360-brand-tag {font-size:14px;margin-top:3px;color:#32527b;}
      .k360-form-divider {height:1px;background:linear-gradient(90deg,transparent,#d0e2f7,transparent);margin:20px 0 22px;}
      .k360-welcome {text-align:center;color:#0c2e5b;font-weight:800;font-size:26px;letter-spacing:-.7px;margin:0 0 5px;}
      .k360-subtitle {text-align:center;color:#627eaa;font-size:13px;margin:0 0 25px;}
      .st-key-login_form_card [data-testid="stForm"] {
        border:0 !important;box-shadow:none !important;padding:0 !important;background:transparent !important;
      }
      .st-key-login_form_card [data-testid="stTextInput"] {margin-bottom:10px !important;}
      .st-key-login_form_card [data-testid="stTextInput"] input {
        border:1px solid #ceddf4 !important;border-radius:10px !important;
        background-color:#f8fbff !important;
        color:#153b6a !important;
        height:51px !important;
        box-shadow:none !important;
        font-size:14px !important;
        padding-left:49px !important;
      }
      .st-key-login_form_card [data-testid="stTextInput"] input::placeholder {color:#7285a8 !important;opacity:1;}
      .st-key-login_form_card [data-testid="stTextInput"] input:focus {border-color:#138dd7 !important;box-shadow:0 0 0 3px rgba(26,140,222,.12) !important;}
      .st-key-login_username input {background-image:url('__USER_SVG__') !important;background-repeat:no-repeat !important;background-position:15px center !important;background-size:19px 19px !important;}
      .st-key-login_password input {background-image:url('__LOCK_SVG__') !important;background-repeat:no-repeat !important;background-position:15px center !important;background-size:19px 19px !important;}
      .st-key-login_form_card button[kind="primaryFormSubmit"] {
        width:100%;min-height:51px;margin-top:7px;border:0!important;
        border-radius:10px!important;
        background:linear-gradient(100deg,#059bd9,#086ac1)!important;
        color:#fff!important;font-size:15px!important;font-weight:750!important;
        box-shadow:0 9px 19px rgba(11,131,202,.19) !important;
      }
      .st-key-login_form_card button[kind="primaryFormSubmit"]:hover {filter:brightness(1.08);border:0!important;}
      .st-key-login_form_card button[kind="primaryFormSubmit"] p {color:#fff !important;font-weight:750 !important;}
      .k360-security {display:flex;align-items:center;justify-content:center;gap:9px;color:#657ca3;font-size:11px;white-space:nowrap;margin-top:20px;}
      .k360-security svg {width:17px;height:17px;flex:0 0 17px;fill:#3e97dd;}
      .st-key-login_form_card [data-testid="stAlert"] {border-radius:10px;margin-top:12px;}
      @media (max-width:1050px) {
        .st-key-login_left,.st-key-login_right{min-height:665px;}
        .st-key-login_left {padding:35px 26px 25px!important;}
        .st-key-login_right {padding:25px 20px!important;}
        .k360-brand-name{font-size:28px;}
        .k360-mark{width:59px;height:59px;flex-basis:59px;}
        .k360-feature-icon{width:50px;height:50px;}
        .k360-feature-icon svg{width:24px;height:24px;}
        .k360-form-brand .k360-brand-name{font-size:28px;}
      }
      @media (max-width:700px) {
        div[data-testid="stHorizontalBlock"]:has(.st-key-login_left){filter:none;}
        .st-key-login_left,.st-key-login_right{min-height:auto;border-radius:19px;}
        .st-key-login_left {padding:29px 23px!important;margin-bottom:14px;}
        .st-key-login_right {padding:24px 15px!important;}
        .k360-left-layout{min-height:480px;}
        .k360-left-copy{padding-top:35px;}
        .k360-headline{font-size:30px;}
        .k360-features{gap:3px;}
        .k360-feature-icon{width:44px;height:44px;border-radius:12px;}
        .k360-feature{font-size:11px;}
        .st-key-login_form_card{padding:28px 18px 17px!important;}
      }
    </style>
    """
    css = (css.replace("__FACTORY_URL__", factory_url)
              .replace("__USER_SVG__", user_svg)
              .replace("__LOCK_SVG__", lock_svg))
    st.markdown(dedent(css), unsafe_allow_html=True)

    logo_svg = """<svg class="k360-mark" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <defs><linearGradient id="k360_login_grad" x1="8" y1="8" x2="56" y2="56" gradientUnits="userSpaceOnUse"><stop stop-color="#079edb"/><stop offset="1" stop-color="#0869ae"/></linearGradient></defs>
      <circle cx="30" cy="34" r="21" stroke="url(#k360_login_grad)" stroke-width="10" stroke-linecap="round" stroke-dasharray="105 30"/>
      <circle cx="30" cy="34" r="11" fill="#eef7ff" stroke="#71c8ed" stroke-width="3"/>
      <circle cx="42" cy="22" r="3.5" fill="#078cc8"/>
      <circle cx="50" cy="14" r="4.5" fill="#0ea5e9"/>
      <path d="M35 29L42 22" stroke="#0284c7" stroke-width="2.5" stroke-linecap="round"/>
    </svg>"""

    # Sol marka anlatımı; SVG ikonlar HTML içinde yalnızca görsel amaçlıdır.
    with st.container():
        left_col, right_col = st.columns([1.12, 0.88], gap="small")
        with left_col:
            with st.container(key="login_left"):
                left_html = """
                <div class="k360-left-layout">
                  <div class="k360-brand">__LOGO__<div>
                    <div class="k360-brand-name">Kalite<span>360</span></div>
                    <div class="k360-brand-tag">Veriden Kaliteye</div>
                  </div></div>
                  <div class="k360-left-copy">
                    <div class="k360-eyebrow"></div>
                    <h1 class="k360-headline">Daha Kaliteli Bir Gelecek İçin <strong>Akıllı Çözümler</strong></h1>
                    <p class="k360-description">Şikâyetlerinizi yönetin, verilerinizi analiz edin,<br>kaliteyi bir adım öteye taşıyın.</p>
                    <div class="k360-features">
                      <div class="k360-feature"><span class="k360-feature-icon"><svg viewBox="0 0 24 24" fill="none" stroke-width="2.4" stroke-linecap="round"><path d="M5 20V11M11 20V5M17 20V8M21 20V3"/></svg></span>Analiz</div>
                      <div class="k360-feature"><span class="k360-feature-icon"><svg viewBox="0 0 24 24" fill="none" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L20 6V12C20 17 16.5 20 12 22C7.5 20 4 17 4 12V6L12 2Z"/><path d="M8.5 12L11 14.5L16 9.5"/></svg></span>Takip</div>
                      <div class="k360-feature"><span class="k360-feature-icon"><svg viewBox="0 0 24 24" fill="none" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.9 4.9l2.2 2.2M16.9 16.9l2.2 2.2M2 12h3M19 12h3M4.9 19.1l2.2-2.2M16.9 7.1l2.2-2.2"/></svg></span>Çözüm</div>
                      <div class="k360-feature"><span class="k360-feature-icon"><svg viewBox="0 0 24 24" fill="none" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l6-6 4 4 7-9"/><path d="M15 6h5v5"/><path d="M4 21V18"/></svg></span>Sürekli İyileştirme</div>
                    </div>
                  </div>
                  <div class="k360-left-footer">Kalite360 · Veriden Kaliteye</div>
                </div>
                """.replace("__LOGO__", logo_svg)
                st.markdown(dedent(left_html), unsafe_allow_html=True)

        with right_col:
            with st.container(key="login_right"):
                with st.container(key="login_form_card"):
                    right_html = """
                    <div class="k360-form-brand">__LOGO__<div>
                      <div class="k360-brand-name">Kalite<span>360</span></div>
                      <div class="k360-brand-tag">Veriden Kaliteye</div>
                    </div></div>
                    <div class="k360-form-divider"></div>
                    <h2 class="k360-welcome">Hoş Geldiniz</h2>
                    <p class="k360-subtitle">Hesabınıza giriş yaparak Kalite360’a erişin</p>
                    """.replace("__LOGO__", logo_svg)
                    st.markdown(dedent(right_html), unsafe_allow_html=True)

                    with st.form("login", clear_on_submit=False):
                        username = st.text_input(
                            "Kullanıcı Adı", placeholder="Kullanıcı Adı",
                            label_visibility="collapsed", key="login_username"
                        )
                        password = st.text_input(
                            "Şifre", type="password", placeholder="Şifre",
                            label_visibility="collapsed", key="login_password"
                        )
                        submitted = st.form_submit_button(
                            "➜  Giriş Yap", type="primary", use_container_width=True
                        )

                    if submitted:
                        u = authenticate(username, password)
                        if u:
                            st.session_state.user = u
                            st.rerun()
                        else:
                            st.error("Kullanıcı adı veya şifre hatalı.")

                    st.markdown(dedent("""
                    <div class="k360-form-divider" style="margin-top:24px;margin-bottom:15px;"></div>
                    <div class="k360-security">
                      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2 20 5v6c0 5.1-3.6 9-8 11-4.4-2-8-5.9-8-11V5l8-3Z"/><path d="m8.2 11.9 2.6 2.5 5.1-5.1" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
                      Güvenli ve Kurumsal Platform
                    </div>
                    """), unsafe_allow_html=True)

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
    <div style="font-size: 1.02rem; font-weight: 800; color: #0f172a; margin-bottom: 3px;">🏭 Kalite360</div>
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
if st.sidebar.button("🚪 Çıkış yap", key="btn_logout_sidebar", use_container_width=True):
    st.session_state.user = None
    st.rerun()

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
