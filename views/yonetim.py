import streamlit as st
from db import db
from services.ai_service import (
    ai_status,
    ensure_translation_columns,
)
from services.mail_service import mail_status, mail_settings, save_mail_settings, PROVIDERS

def render():
    ensure_translation_columns()

    st.subheader("Bağlantı ve Servis Durumu")
    local_ai_ok, local_ai_message = ai_status()
    smtp_ok, smtp_msg = mail_status()

    a, b, c = st.columns(3)
    a.metric("Yerel yapay zekâ", "Hazır" if local_ai_ok else "Kurulum gerekiyor")
    b.metric("E-posta (SMTP)", "Yapılandırılmış" if smtp_ok else "İsteğe bağlı")
    c.metric("Türkçe Çeviri Modülü", "Aktif (Detay Sayfasında)")
    st.caption(local_ai_message)
    st.caption(smtp_msg)

    st.divider()

    st.subheader("🌐 Çeviri Veritabanı Yönetimi")
    st.caption("Detay sayfasında yaptığınız tekil çeviriler veritabanına kalıcı olarak işlenir. Buradan mevcut durumu görüntüleyebilir veya çevirileri sıfırlayabilirsiniz.")

    with st.container(border=True):
        with db() as conn:
            total_cnt = conn.execute("SELECT COUNT(*) FROM complaints").fetchone()[0]
            translated_cnt = conn.execute(
                "SELECT COUNT(*) FROM complaints WHERE tr_description IS NOT NULL AND tr_description != ''"
            ).fetchone()[0]

        col_tr1, col_tr2 = st.columns([2, 1])
        with col_tr1:
            st.metric("Veritabanındaki Çevrilmiş Kayıt Sayısı", f"{translated_cnt} / {total_cnt}")
        with col_tr2:
            st.write("")
            if st.button("🗑️ Tüm Çevirileri Sıfırla", type="secondary", use_container_width=True):
                with db() as conn:
                    conn.execute("UPDATE complaints SET tr_description='', tr_action_plan=''")
                    conn.commit()
                st.warning("Tüm kayıtlı çeviriler sıfırlandı. Detay sayfasından tekrar çevirebilirsiniz.")
                st.rerun()

    st.divider()
    
    # 🧹 YENİ EKLENEN BÖLÜM: ÇÖP KAYITLARI TEMİZLEME
    st.subheader("🧹 Veritabanı Bakımı ve Çöp Kayıt Temizliği")
    st.caption("Açıklama ve çözüm metni bulunmayan, analiz değeri taşımayan boş FDA kayıtlarını tek tuşla veritabanından kalıcı olarak silebilirsiniz.")
    
    with st.container(border=True):
        if st.button("🗑️ İçeriksiz/Boş FDA Kayıtlarını Temizle", type="primary"):
            with db() as conn:
                cur = conn.cursor()
                # Hem description'ı bizim varsayılan metnimiz olan, hem de action_plan'ı boş olanları siler
                cur.execute("DELETE FROM complaints WHERE description = 'FDA MAUDE kaydında olay metni belirtilmemiş.' AND (action_plan = '' OR action_plan IS NULL)")
                deleted_count = cur.rowcount
                conn.commit()
            if deleted_count > 0:
                st.success(f"🎉 Temizlik başarılı! Toplam {deleted_count} adet boş/çöp kayıt veritabanından silindi.")
                st.rerun()
            else:
                st.info("Veritabanında silinecek içeriksiz/boş kayıt bulunmuyor.")

    st.divider()
    
    st.subheader("Şirket e-posta ayarları (isteğe bağlı)")
    current = mail_settings()
    providers = list(PROVIDERS.keys())
    provider_default = current.get("provider", providers[0])
    if provider_default not in providers: 
        provider_default = providers[0]
        
    with st.form("mail_settings_form"):
        provider = st.selectbox("Sağlayıcı", providers + ["Diğer / özel SMTP"], index=(providers + ["Diğer / özel SMTP"]).index(provider_default) if provider_default in providers + ["Diğer / özel SMTP"] else 0)
        defaults = PROVIDERS.get(provider, {"host": current.get("host", ""), "port": current.get("port", "587")})
        host = st.text_input("SMTP sunucusu", value=defaults.get("host") or current.get("host", ""))
        port = st.selectbox("Port", ["587", "465"], index=0 if str(defaults.get("port", current.get("port", "587"))) == "587" else 1)
        smtp_user = st.text_input("Kullanıcı adı / e-posta", value=current.get("username", ""))
        smtp_password = st.text_input("Uygulama parolası", value=current.get("password", ""), type="password")
        mail_from = st.text_input("Gönderen adresi", value=current.get("sender", ""))
        notify_to = st.text_input("İç bildirim alıcısı", value=current.get("notify_to", ""))
        save_mail = st.form_submit_button("E-posta ayarlarını kaydet", type="primary")
        
    if save_mail:
        save_mail_settings(provider, host, port, smtp_user, smtp_password, mail_from, notify_to)
        st.success("E-posta ayarları kaydedildi.")
        st.rerun()

    st.divider()
    st.write("Pilot hesaplar: musterihizmetleri / MH2026! · kalite / Kalite2026! · admin / Admin2026!")