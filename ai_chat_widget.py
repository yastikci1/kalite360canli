import json
import streamlit as st
import streamlit.components.v1 as components
from db import complaints, get_complaint
from ai_quality_engine import render_quality_engine_tab
from utils import (
    get_ollama_status,
    test_gemini_connection,
    fetch_available_gemini_models,
    DEFAULT_GEMINI_MODEL,
)

from ai_response_drafter import (
    RESPONSE_PURPOSES,
    CHANNELS,
    TONES,
    LENGTHS,
    AUTHORIZED_ROLES,
    DEFAULT_COMPANY_SETTINGS,
    init_assistant_tables,
    get_company_settings,
    save_company_settings,
    load_user_chat_history,
    save_chat_message,
    clear_user_chat_history,
    save_draft_to_db,
    get_saved_drafts,
    is_user_authorized,
    inspect_complaint_context,
    generate_customer_response_draft,
)
from ai_agent_engine import process_ai_query


def inject_custom_css(is_fullscreen: bool = False):
    if is_fullscreen:
        popover_pos_css = """
        div[data-baseweb="popover"] {
            position: fixed !important;
            top: 10px !important;
            left: 10px !important;
            right: 10px !important;
            bottom: 90px !important;
            transform: none !important;
            width: calc(100vw - 20px) !important;
            height: calc(100vh - 100px) !important;
            z-index: 999999 !important;
        }

        div[data-baseweb="popover"] > div,
        div[data-testid="stPopoverBody"] {
            background-color: #ffffff !important;
            background: #ffffff !important;
            color: #0f172a !important;
            width: calc(100vw - 20px) !important;
            min-width: calc(100vw - 20px) !important;
            max-width: calc(100vw - 20px) !important;
            height: calc(100vh - 100px) !important;
            max-height: calc(100vh - 100px) !important;
            border-radius: 18px !important;
            border: 1px solid #cbd5e1 !important;
            box-shadow: 0 22px 55px rgba(15, 23, 42, 0.35), 0 4px 14px rgba(15, 23, 42, 0.18) !important;
            padding: 18px 26px !important;
            overflow-y: auto !important;
            backdrop-filter: none !important;
        }
        """
    else:
        popover_pos_css = """
        div[data-baseweb="popover"] {
            z-index: 999999 !important;
        }

        div[data-baseweb="popover"] > div,
        div[data-testid="stPopoverBody"] {
            background-color: #ffffff !important;
            background: #ffffff !important;
            color: #0f172a !important;
            width: 50vw !important;
            min-width: 560px !important;
            max-width: calc(100vw - 32px) !important;
            height: 84vh !important;
            max-height: calc(100vh - 60px) !important;
            border-radius: 18px !important;
            border: 1px solid #cbd5e1 !important;
            box-shadow: 0 22px 55px rgba(15, 23, 42, 0.30), 0 4px 14px rgba(15, 23, 42, 0.14) !important;
            padding: 16px 20px !important;
            overflow-y: auto !important;
            backdrop-filter: none !important;
        }
        """

    st.markdown(f"""
    <style>
        div.element-container:has(div[data-testid="stPopover"]),
        div[data-testid="stElementContainer"]:has(div[data-testid="stPopover"]) {{
            position: fixed !important;
            bottom: 24px !important;
            right: 24px !important;
            width: 64px !important;
            height: 64px !important;
            z-index: 999990 !important;
            margin: 0 !important;
            padding: 0 !important;
        }}

        div[data-testid="stPopover"] {{
            position: fixed !important;
            bottom: 24px !important;
            right: 24px !important;
            width: 64px !important;
            height: 64px !important;
            z-index: 999990 !important;
            margin: 0 !important;
            padding: 0 !important;
        }}

        div[data-testid="stPopover"] > div {{
            width: 64px !important;
            height: 64px !important;
        }}

        div[data-testid="stPopover"] button,
        button[data-testid="stPopoverButton"] {{
            width: 64px !important;
            min-width: 64px !important;
            max-width: 64px !important;
            height: 64px !important;
            min-height: 64px !important;
            max-height: 64px !important;
            border-radius: 50% !important;
            background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%) !important;
            color: #ffffff !important;
            border: 2px solid #ffffff !important;
            box-shadow: 0 8px 24px rgba(37, 99, 235, 0.45), 0 2px 6px rgba(0, 0, 0, 0.2) !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            padding: 0 !important;
            margin: 0 !important;
            cursor: pointer !important;
            transition: transform 0.2s ease, box-shadow 0.2s ease !important;
        }}

        div[data-testid="stPopover"] button:hover,
        button[data-testid="stPopoverButton"]:hover {{
            transform: scale(1.08) !important;
            background: linear-gradient(135deg, #1d4ed8 0%, #1e3a8a 100%) !important;
            box-shadow: 0 12px 28px rgba(37, 99, 235, 0.6) !important;
        }}

        div[data-testid="stPopover"] button p,
        button[data-testid="stPopoverButton"] p {{
            font-size: 28px !important;
            line-height: 1 !important;
            margin: 0 !important;
            padding: 0 !important;
        }}

        div[data-testid="stPopover"] button svg,
        button[data-testid="stPopoverButton"] svg {{
            display: none !important;
        }}

        {popover_pos_css}

        div[data-testid="stPopoverBody"] .stButton > button {{
            border-radius: 8px !important;
            font-size: 12px !important;
            font-weight: 600 !important;
            padding: 5px 8px !important;
            min-height: 32px !important;
            height: auto !important;
            background-color: #f8fafc !important;
            color: #1e293b !important;
            border: 1px solid #cbd5e1 !important;
            width: 100% !important;
        }}

        div[data-testid="stPopoverBody"] .stButton > button:hover {{
            background-color: #eff6ff !important;
            border-color: #2563eb !important;
            color: #1d4ed8 !important;
        }}

        div[data-testid="stPopoverBody"] div[data-testid="stChatMessage"] {{
            background-color: #f8fafc !important;
            border: 1px solid #e2e8f0 !important;
            border-radius: 12px !important;
            padding: 10px 12px !important;
            margin-bottom: 8px !important;
        }}
    </style>
    """, unsafe_allow_html=True)


def render_copy_button(text_to_copy: str, key_suffix: str):
    escaped = json.dumps(text_to_copy)
    components.html(
        f"""
        <div style="margin:0; padding:0;">
            <button id="copy_btn_{key_suffix}" onclick='navigator.clipboard.writeText({escaped}).then(() => {{
                const b = document.getElementById("copy_btn_{key_suffix}");
                b.innerText = "✅ Panoya Kopyalandı!";
                b.style.backgroundColor = "#dcfce7";
                b.style.borderColor = "#16a34a";
                b.style.color = "#15803d";
                setTimeout(() => {{
                    b.innerText = "📋 Metni Panoya Kopyala";
                    b.style.backgroundColor = "#f8fafc";
                    b.style.borderColor = "#cbd5e1";
                    b.style.color = "#1e293b";
                }}, 2000);
            }})' style="width:100%; height:34px; border-radius:8px; border:1px solid #cbd5e1; background:#f8fafc; color:#1e293b; font-family:sans-serif; font-size:12.5px; font-weight:600; cursor:pointer;">
                📋 Metni Panoya Kopyala
            </button>
        </div>
        """,
        height=40,
    )


def render_chat():
    if "ai_chat_fullscreen" not in st.session_state:
        st.session_state.ai_chat_fullscreen = False
    if "use_ollama_ai" not in st.session_state:
        st.session_state.use_ollama_ai = True

    is_fs = st.session_state.ai_chat_fullscreen
    inject_custom_css(is_fs)
    init_assistant_tables()

    user = st.session_state.get("user") or {}
    username = user.get("username", "misafir")

    if "ai_chat_history" not in st.session_state or st.session_state.get("ai_chat_user") != username:
        st.session_state.ai_chat_history = load_user_chat_history(username)
        st.session_state.ai_chat_user = username

    ollama_info = get_ollama_status()

    with st.popover("🤖", help="Kalite360 Yapay Zekâ ve Müşteri İletişim Asistanı"):
        hdr_c1, hdr_c2 = st.columns([7.5, 1.3] if is_fs else [4.3, 1.2])
        with hdr_c1:
            if ollama_info["available"] and st.session_state.use_ollama_ai:
                eng_lbl = ollama_info["active_engine"]
                mdl_lbl = st.session_state.get("selected_ollama_model") if eng_lbl == "Ollama" else ollama_info["active_model"]
                mdl_lbl = mdl_lbl or ollama_info["active_model"]
                st.markdown(
                    f"##### 🤖 Kalite360 AI Asistanı <span style='font-size:11px; background:#dcfce7; color:#15803d; padding:2px 7px; border-radius:10px;'>🟢 {eng_lbl}: {mdl_lbl}</span>",
                    unsafe_allow_html=True
                )
            else:
                st.markdown(
                    "##### 🤖 Kalite360 AI Asistanı <span style='font-size:11px; background:#fef3c7; color:#b45309; padding:2px 7px; border-radius:10px;'>⚡ Yerleşik NLP (AI Ayarlarından Model Seçin)</span>",
                    unsafe_allow_html=True
                )
        with hdr_c2:
            fs_label = "🗗 Küçült" if is_fs else "⛶ Tam Ekran"
            fs_help = "Yarım ekran boyutuna geri dön (Küçült)" if is_fs else "Tam ekran yap (Büyüt)"
            if st.button(fs_label, key="btn_toggle_ai_fullscreen", help=fs_help, use_container_width=True):
                st.session_state.ai_chat_fullscreen = not is_fs
                st.rerun()

        tab_chat, tab_draft, tab_qe = st.tabs([
            "💬 Sohbet & Analiz",
            "✉️ Müşteri Geri Dönüşü Oluştur",
            "🧩 6M & DÖF / 8D"
        ])

        with tab_chat:
            top_c1, top_c2, top_c3 = st.columns([2.7, 1.1, 0.9])
            top_c1.caption("Canlı SQL & RAG Destekli Kalite Mühendisi Yapay Zekâsı")
            st.session_state.use_ollama_ai = top_c2.toggle(
                "🧠 AI Aktif",
                value=st.session_state.use_ollama_ai,
                key="tgl_ollama_mode",
                help="Açık olduğunda sabit şablonlar yerine yapay zekâ veritabanını analiz ederek doğrudan yanıt verir."
            )
            if top_c3.button("🗑️ Temizle", key="btn_clear_ai_chat"):
                clear_user_chat_history(username)
                st.session_state.ai_chat_history = load_user_chat_history(username)
                st.session_state.pop("ai_last_entities", None)
                st.rerun()

            with st.expander("🧠 Yapay Zekâ Beyin & Model Ayarları (Zekâ Seviyesini Yükselt)", expanded=not ollama_info["available"]):
                saved_cfg = ollama_info.get("config", {})
                prov_options = ["auto", "gemini", "openai_compat", "ollama"]
                prov_labels = {
                    "auto": "🔄 Otomatik (Bulut API varsa onu, yoksa Yerel Ollama'yı kullan)",
                    "gemini": "✨ Google Gemini API (Çok Yüksek Türkçe Zekâsı - Ücretsiz API Destekli)",
                    "openai_compat": "🚀 Groq / OpenAI / DeepSeek API (Llama-3.3-70B / GPT-4o Seviyesi)",
                    "ollama": "💻 Yerel Ollama (İnternetsiz Bilgisayar Üzerinde Çalışır)"
                }
                curr_prov = saved_cfg.get("ai_provider", "auto")
                if curr_prov not in prov_options:
                    curr_prov = "auto"

                sel_prov = st.selectbox(
                    "Aktif Yapay Zekâ Motoru:",
                    options=prov_options,
                    index=prov_options.index(curr_prov),
                    format_func=lambda x: prov_labels.get(x, x),
                    key="ui_ai_provider_sel"
                )

                if sel_prov in ["gemini", "auto"]:
                    st.markdown("**1. Google Gemini Ayarları:**")
                    g_c1, g_c2 = st.columns([1.8, 1.2])
                    gem_key = g_c1.text_input(
                        "Gemini API Anahtarı (aistudio.google.com üzerinden alınır):",
                        value=saved_cfg.get("gemini_api_key", ""),
                        type="password",
                        key="ui_gem_key"
                    )

                    # Anahtarda gerçekten açık olan modelleri otomatik listele
                    avail_gem_models, _ = fetch_available_gemini_models(gem_key) if gem_key else ([], "")
                    if not avail_gem_models:
                        avail_gem_models = [
                            "gemini-3-flash-preview",
                            "gemma-4-31b-it",
                            "gemma-4-26b-a4b-it",
                            "gemini-flash-lite-latest",
                            "gemini-2.5-flash-lite",
                        ]

                    curr_gem_mdl = saved_cfg.get("gemini_model", DEFAULT_GEMINI_MODEL)
                    if curr_gem_mdl not in avail_gem_models:
                        curr_gem_mdl = avail_gem_models[0]

                    gem_mdl = g_c2.selectbox(
                        "Gemini Modeli (Anahtarınızda Açık Modeller):",
                        options=avail_gem_models,
                        index=avail_gem_models.index(curr_gem_mdl),
                        key="ui_gem_mdl_select"
                    )

                    if st.button("🔌 Gemini Bağlantısını Test Et", key="btn_test_gem_conn", use_container_width=True):
                        ok_test, msg_test, avail_models = test_gemini_connection(gem_key, gem_mdl)
                        if ok_test:
                            st.success(f"✅ {msg_test}")
                        else:
                            st.error(f"❌ {msg_test}")
                            if avail_models:
                                st.info(f"💡 Anahtarınızda açık olan modeller: `{', '.join(avail_models[:6])}`")
                else:
                    gem_key = saved_cfg.get("gemini_api_key", "")
                    gem_mdl = saved_cfg.get("gemini_model", DEFAULT_GEMINI_MODEL)

                if sel_prov == "openai_compat":
                    st.markdown("**2. Groq / OpenAI / DeepSeek API Ayarları:**")
                    o_c1, o_c2, o_c3 = st.columns([1.5, 1.5, 1])
                    oa_key = o_c1.text_input("API Key:", value=saved_cfg.get("openai_api_key", ""), type="password", key="ui_oa_key")
                    oa_url = o_c2.text_input("Base URL:", value=saved_cfg.get("openai_base_url", "https://api.groq.com/openai/v1"), key="ui_oa_url")
                    oa_mdl = o_c3.text_input("Model:", value=saved_cfg.get("openai_model", "llama-3.3-70b-versatile"), key="ui_oa_mdl")
                else:
                    oa_key = saved_cfg.get("openai_api_key", "")
                    oa_url = saved_cfg.get("openai_base_url", "https://api.groq.com/openai/v1")
                    oa_mdl = saved_cfg.get("openai_model", "llama-3.3-70b-versatile")

                if ollama_info["ollama_available"]:
                    sel_ol_mdl = st.selectbox(
                        "Yerel Ollama Modeli:",
                        options=ollama_info["models"],
                        index=ollama_info["models"].index(ollama_info["active_model"]) if ollama_info["active_model"] in ollama_info["models"] else 0,
                        key="ui_ollama_mdl_sel"
                    )
                    st.session_state["selected_ollama_model"] = sel_ol_mdl
                else:
                    sel_ol_mdl = saved_cfg.get("ollama_model", "qwen2.5:7b")

                if st.button("💾 Yapay Zekâ Motor Ayarlarını Kaydet", key="btn_save_ai_engine_cfg", use_container_width=True):
                    save_company_settings({
                        "ai_ai_provider": sel_prov,
                        "ai_gemini_api_key": gem_key.strip(),
                        "ai_gemini_model": gem_mdl.strip() or DEFAULT_GEMINI_MODEL,
                        "ai_openai_api_key": oa_key.strip(),
                        "ai_openai_base_url": oa_url.strip(),
                        "ai_openai_model": oa_mdl.strip(),
                        "ai_ollama_model": sel_ol_mdl,
                    })
                    st.success("✅ Yapay Zekâ motor ayarları kaydedildi!")
                    st.rerun()

            msg_container = st.container(height=560 if is_fs else 420, border=True)
            with msg_container:
                for msg in st.session_state.ai_chat_history:
                    with st.chat_message(msg["role"]):
                        st.markdown(msg["content"])

            st.markdown("<div style='font-size:11.5px; font-weight:600; color:#475569; margin:3px 0;'>⚡ Hızlı Analiz & Müşteri Yanıt Komutları:</div>", unsafe_allow_html=True)

            c_prompt = None
            r1c1, r1c2 = st.columns(2)
            if r1c1.button("📊 En Çok Şikâyet", key="qa_1"):
                c_prompt = "Bu ay en çok hangi ürün için şikâyet aldık?"
            if r1c2.button("⚠️ En Sık Hatalar", key="qa_2"):
                c_prompt = "En sık karşılaşılan hata nedenlerini analiz et."

            r2c1, r2c2 = st.columns(2)
            if r2c1.button("🧩 6M Kök Neden", key="qa_3"):
                c_prompt = "Bu şikâyet için 6M kök neden analizi hazırla."
            if r2c2.button("📋 Açık & Gecikmiş DÖF", key="qa_4"):
                c_prompt = "Açık ve gecikmiş DÖF kayıtlarını listele."

            r3c1, r3c2 = st.columns(2)
            if r3c1.button("📈 30 Günlük Trend", key="qa_5"):
                c_prompt = "Son 30 günlük şikâyet trendini değerlendir."
            if r3c2.button("✉️ Geri Dönüş Hazırla", key="qa_6"):
                c_prompt = "Bu şikâyete uygun bir müşteri geri dönüş metni hazırla."

            r4c1, r4c2 = st.columns(2)
            if r4c1.button("⏳ İnceleme Sürüyor Maili", key="qa_7"):
                c_prompt = "Müşteriye incelemenin devam ettiğini bildiren bir e-posta yaz."
            if r4c2.button("📸 Eksik Bilgi Talep Et", key="qa_8"):
                c_prompt = "Müşteriden eksik teknik bilgileri talep eden bir mesaj oluştur."

            with st.form("ai_chat_send_form", clear_on_submit=True):
                in_col, btn_col = st.columns([4.2, 1])
                user_input = in_col.text_input(
                    "Mesaj",
                    placeholder="Bana istediğin gibi sor: 'Pareto analizi nedir?', 'Kök neden nedir?'...",
                    label_visibility="collapsed"
                )
                send_clicked = btn_col.form_submit_button("➤", use_container_width=True)

            active_prompt = c_prompt if c_prompt else (user_input.strip() if send_clicked and user_input.strip() else None)

            if active_prompt:
                st.session_state.ai_chat_history.append({"role": "user", "content": active_prompt})
                save_chat_message(username, "user", active_prompt)

                with msg_container:
                    with st.chat_message("user"):
                        st.markdown(active_prompt)
                    with st.chat_message("assistant"):
                        spinner_txt = f"Kalite360 veritabanı ve {ollama_info['active_engine']} analiz ediyor..." if (ollama_info["available"] and st.session_state.use_ollama_ai) else "Kalite360 veritabanı değerlendiriliyor..."
                        with st.spinner(spinner_txt):
                            try:
                                reply = process_ai_query(active_prompt, user)
                            except Exception as exc:
                                reply = f"❌ İşlem sırasında bir hata oluştu: `{exc}`"
                            st.markdown(reply)

                st.session_state.ai_chat_history.append({"role": "assistant", "content": reply})
                save_chat_message(username, "assistant", reply)
                st.rerun()

        with tab_draft:
            ok_auth, auth_msg = is_user_authorized(user)
            if not ok_auth:
                st.error(f"🔒 {auth_msg}")
                return

            df_all = complaints()
            if df_all.empty:
                st.info("Sistemde henüz kayıtlı şikâyet bulunmuyor.")
                return

            active_detail_id = st.session_state.get("detail_id")
            if active_detail_id and st.session_state.get("_last_synced_detail_id") != active_detail_id:
                st.session_state["draft_selected_cid"] = int(active_detail_id)
                st.session_state["_last_synced_detail_id"] = int(active_detail_id)

            id_list = df_all["id"].astype(int).tolist()
            default_cid = st.session_state.get("draft_selected_cid")
            if default_cid not in id_list:
                default_cid = int(active_detail_id) if (active_detail_id and int(active_detail_id) in id_list) else id_list[0]

            label_map = {
                int(r["id"]): f"{r['complaint_no']} · {str(r['product'])[:20]} ({r['status']})"
                for _, r in df_all.iterrows()
            }

            selected_cid = st.selectbox(
                "📌 Şikâyet Kaydı (Detay sayfasından otomatik seçilir):",
                options=id_list,
                index=id_list.index(default_cid),
                format_func=lambda x: label_map.get(x, str(x)),
                key="draft_cid_selectbox"
            )
            st.session_state["draft_selected_cid"] = int(selected_cid)
            current_item = get_complaint(int(selected_cid))
            ctx_preview = inspect_complaint_context(current_item)

            st.caption(
                f"**Müşteri:** `{ctx_preview['customer']}` | **Durum:** `{ctx_preview['status']}` | "
                f"**Öncelik:** `{ctx_preview['priority']}` | **Önerilen Tür:** `{ctx_preview['auto_purpose'][:28]}...`"
            )

            purpose_sel = st.selectbox(
                "🎯 Geri Dönüş Amacı:",
                RESPONSE_PURPOSES,
                index=RESPONSE_PURPOSES.index(st.session_state.get("draft_purpose", RESPONSE_PURPOSES[0]))
                if st.session_state.get("draft_purpose") in RESPONSE_PURPOSES else 0,
                key="ui_draft_purpose"
            )

            col_ch, col_tn, col_ln = st.columns(3)
            channel_sel = col_ch.selectbox("📡 Kanal:", CHANNELS, key="ui_draft_channel")
            tone_sel = col_tn.selectbox("🎭 Üslup:", TONES, key="ui_draft_tone")
            length_sel = col_ln.selectbox("📏 Uzunluk:", LENGTHS, index=1, key="ui_draft_length")

            extra_inst = st.text_input(
                "📝 Ek Talimat (İsteğe bağlı):",
                placeholder="Örn: Cihazın kargo kodu istensin, iade sözü verilmesin...",
                key="ui_draft_extra"
            )

            editor_key = f"draft_editor_text_{selected_cid}"
            meta_key = f"draft_last_meta_{selected_cid}"

            if editor_key not in st.session_state:
                init_res = generate_customer_response_draft(
                    current_item, purpose_sel, channel_sel, tone_sel, length_sel, extra_inst
                )
                st.session_state[editor_key] = init_res["draft_text"]
                st.session_state[meta_key] = init_res

            b1, b2, b3, b4, b5 = st.columns(5)
            trigger_regen = None
            if b1.button("🔄 Oluştur", help="Seçili ayarlarla metni yeniden oluştur", key="btn_gen_main"):
                trigger_regen = (purpose_sel, channel_sel, tone_sel, length_sel, False)
            if b2.button("✂️ Daha Kısa", help="Metni daha kısa ve öz hale getir", key="btn_gen_short"):
                trigger_regen = (purpose_sel, channel_sel, "Kısa ve Net", "Kısa", False)
            if b3.button("🤝 Empatik", help="Daha empatik ve yapıcı bir dil kullan", key="btn_gen_emp"):
                trigger_regen = (purpose_sel, channel_sel, "Empatik", length_sel, False)
            if b4.button("⚙️ Teknik", help="Daha teknik ve mühendislik odaklı yaz", key="btn_gen_tech"):
                trigger_regen = (purpose_sel, channel_sel, "Teknik", "Detaylı", False)
            if b5.button("🧠 AI ile Yaz", help="Aktif yapay zekâ modelini kullanarak talimatınıza göre doğal dille yeniden yaz", key="btn_gen_llm"):
                trigger_regen = (purpose_sel, channel_sel, tone_sel, length_sel, True)

            if trigger_regen:
                p_arg, ch_arg, tn_arg, ln_arg, llm_flag = trigger_regen
                new_res = generate_customer_response_draft(
                    current_item, p_arg, ch_arg, tn_arg, ln_arg, extra_inst, use_llm=llm_flag
                )
                st.session_state[editor_key] = new_res["draft_text"]
                st.session_state[meta_key] = new_res
                st.rerun()

            last_meta = st.session_state.get(meta_key) or generate_customer_response_draft(current_item)

            for w in last_meta.get("guardrail_warnings", []):
                st.warning(w)

            if ctx_preview["missing_fields"]:
                q_text_info = f"\n\n❓ *{ctx_preview['clarifying_questions'][0]}*" if ctx_preview["clarifying_questions"] else ""
                st.info(
                    f"🔍 **Eksik Kayıt Kontrolü:** `{', '.join(ctx_preview['missing_fields'])}` veritabanında bulunmadığı için **uydurulmamıştır**.{q_text_info}"
                )

            edited_text = st.text_area(
                "✏️ Oluşturulan Müşteri Geri Dönüş Taslağı (Düzenlenebilir):",
                height=420 if is_fs else 300,
                key=editor_key,
                help="Metin üzerinde yaptığınız düzenlemeler otomatik korunur. Onayınız olmadan hiçbir mesaj gönderilmez."
            )

            act_c1, act_c2 = st.columns(2)
            with act_c1:
                render_copy_button(edited_text, f"cid_{selected_cid}")
            with act_c2:
                if st.button("💾 Taslak Olarak Kaydet", key="btn_save_draft_db", use_container_width=True):
                    draft_id = save_draft_to_db(
                        cid=int(selected_cid),
                        c_no=ctx_preview["complaint_no"],
                        purpose=last_meta.get("resolved_purpose", purpose_sel),
                        channel=last_meta.get("channel", channel_sel),
                        tone=last_meta.get("tone", tone_sel),
                        length_opt=last_meta.get("length_opt", length_sel),
                        extra_inst=extra_inst,
                        subject=last_meta.get("subject", ""),
                        body_text=edited_text,
                        source_summary={
                            "complaint_no": ctx_preview["complaint_no"],
                            "status": ctx_preview["status"],
                            "priority": ctx_preview["priority"],
                            "has_root_cause": ctx_preview["has_root_cause"],
                            "has_action_plan": ctx_preview["has_action_plan"],
                            "missing_fields": ctx_preview["missing_fields"]
                        },
                        username=username
                    )
                    st.success(f"✅ Taslak #{draft_id} `{ctx_preview['complaint_no']}` kaydına işlendi! (Otomatik gönderim yapılmadı, kayıt kapatılmadı.)")

            with st.expander("🔍 Metnin Dayandığı Şikâyet Verileri & Kayıtlı Taslaklar"):
                plan_preview_str = (ctx_preview["action_plan"][:140] + "...") if ctx_preview["action_plan"] else "Veritabanında kayıtlı değil"
                corp_label_str = "Kurumsal" if ctx_preview["is_corporate"] else "Bireysel"
                st.markdown(
                    f"- **Şikâyet No & Tarih:** `{ctx_preview['complaint_no']}` ({ctx_preview['created_date']} · {ctx_preview['elapsed_days']} gün)\n"
                    f"- **Müşteri & Muhatap:** `{ctx_preview['customer']}` / `{ctx_preview['contact'] or '-'}` ({corp_label_str})\n"
                    f"- **İletişim:** `{ctx_preview['email']}` · `{ctx_preview['phone']}`\n"
                    f"- **Ürün & Seri/MDR No:** `{ctx_preview['product']}` · `{ctx_preview['order_no'] or 'Belirtilmemiş'}`\n"
                    f"- **Durum & Geçerlilik:** `{ctx_preview['status']}` · `{ctx_preview['validity']}`\n"
                    f"- **Doğrulanmış Kök Neden:** `{ctx_preview['root_cause'] or 'Veritabanında kayıtlı değil'}`\n"
                    f"- **Teknik Servis Planı:** `{plan_preview_str}`\n"
                    f"- **Önceki İletişim Sayısı:** `{ctx_preview['prev_contacts_count']}` kayıt | **Açık DÖF:** `{len(ctx_preview['open_actions'])}` adet"
                )
                saved_list = get_saved_drafts(int(selected_cid))
                if saved_list:
                    st.divider()
                    st.markdown("**📂 Bu Şikâyet İçin Kaydedilmiş Son Taslaklar:**")
                    for d_row in saved_list[:3]:
                        st.caption(f"• **#{d_row['id']}** ({d_row['created_at'][:16]}) - {d_row['purpose']} [{d_row['channel']} / {d_row['tone']}] - Kaydeden: `{d_row['created_by']}`")
                        if st.button(f"🔄 Taslak #{d_row['id']} Metnini Editöre Yükle", key=f"load_dr_{d_row['id']}"):
                            st.session_state[editor_key] = d_row["draft_text"]
                            st.rerun()

            with st.expander("🏢 Kurumsal İmza ve Standart İletişim Ayarları"):
                curr_comp = get_company_settings()
                c_name = st.text_input("Şirket Unvanı", value=curr_comp["company_name"], key="cfg_cname")
                c_sign = st.text_area("Standart Kapanış / İmza", value=curr_comp["closing_template"], height=68, key="cfg_csign")
                c_info = st.text_input("İletişim Bilgileri", value=curr_comp["contact_info"], key="cfg_cinfo")
                if st.button("💾 Kurumsal İletişim Ayarlarını Kaydet", key="btn_save_comp_cfg"):
                    save_company_settings({
                        "company_name": c_name.strip(),
                        "closing_template": c_sign.strip(),
                        "contact_info": c_info.strip()
                    })
                    st.success("Kurumsal imza ayarları güncellendi.")

        with tab_qe:
            render_quality_engine_tab(user)