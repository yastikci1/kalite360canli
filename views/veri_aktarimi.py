import streamlit as st
import pandas as pd
import io
import os
from datetime import date
from db import db
from services.fetch_fda import fetch_dynamic_fda
from services.email_ingest import settings as imap_settings, save_settings as save_imap_settings, poll_inbox
from services.mail_service import load_local_env

DEFAULT_MODEL_NUMBERS = "EM800, MR8-AF02"

def get_saved_model_numbers():
    load_local_env()
    raw = os.getenv("SAVED_FDA_MODEL_NUMBERS", DEFAULT_MODEL_NUMBERS)
    return [c.strip().upper() for c in raw.split(",") if c.strip()]

def add_model_to_saved_list(new_model: str):
    m = new_model.strip().upper()
    if not m:
        return False
    current = get_saved_model_numbers()
    if m not in current:
        current.append(m)
        cleaned_str = ", ".join(current)
        save_imap_settings({"SAVED_FDA_MODEL_NUMBERS": cleaned_str})
        return True
    return False

def render(user):
    st.subheader("Excel'den şikâyet aktarımı")
    template = pd.DataFrame([{
        "Müşteri/Firma": "Örnek Firma", "İlgili Kişi": "Ayşe Yılmaz", "Telefon": "05551234567",
        "E-posta": "ayse@ornek.com", "Ürün": "P-104 mil", "Sipariş No": "SO-2026-1234",
        "Kategori": "Ölçü / Tolerans", "Öncelik": "Orta", "Açıklama": "Ürün çapı tolerans dışında ölçülmüş.",
        "Talep": "İnceleme ve dönüş", "Sorumlu": "kalite", "Acil Geri Dönüş": "Hayır", "Kayıt Tarihi": "2026-09-15"
    }])
    
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer: 
        template.to_excel(writer, index=False, sheet_name="Sikayetler")
    st.download_button("Excel şablonunu indir", buf.getvalue(), file_name="kalite360_sikayet_sablonu.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    
    upload = st.file_uploader("Excel veya CSV dosyası", type=["xlsx", "xls", "csv"], key="complaint_upload")
    if upload:
        try:
            imported_df = pd.read_csv(upload) if upload.name.lower().endswith('.csv') else pd.read_excel(upload)
            st.caption(f"Önizleme: {len(imported_df)} satır")
            st.dataframe(imported_df.head(10), use_container_width=True, hide_index=True)
        except Exception as exc: 
            st.error(f"Dosya okunamadı: {exc}")

    st.divider()
    st.subheader("🌐 Dinamik FDA MAUDE Veritabanı Arama ve Senkronizasyon")
    st.caption("Aşağıdaki filtreleri kullanarak açık FDA veritabanında canlı arama yapın. Sistem, mevcut şikâyetleri 'MDR Numarası' üzerinden kontrol eder; sadece veritabanınızda olmayan YENİ kayıtları indirir ve eski kayıtların üzerine yazmaz.")
    
    if st.session_state.get("fda_sync_msg"):
        msg_type, msg_text = st.session_state.pop("fda_sync_msg")
        if msg_type == "success":
            st.success(msg_text)
        else:
            st.info(msg_text)

    saved_models = get_saved_model_numbers()
    
    with st.container(border=True):
        st.markdown("##### ⚡ Kayıtlı Model Numaralarını (Model Number) Yönet ve Toplu Güncelle")
        st.caption(f"Takip edilen sabit Model Numaraları: **{', '.join(saved_models) if saved_models else 'Tanımlı model yok'}**")
        
        q_col1, q_col2 = st.columns([2, 1])
        with q_col1:
            # LİMİT ARTTIRILDI: Seçenekler 5000'e kadar çıkarıldı
            batch_limit = st.select_slider(
                "Model Numarası başına taranacak maksimum kayıt sayısı",
                options=[50, 100, 250, 500, 1000, 2500, 5000],
                value=500,
                key="batch_fda_limit"
            )
        with q_col2:
            st.write("")
            btn_batch_sync = st.button("🚀 Tüm Modelleri Şimdi Güncelle", type="primary", use_container_width=True)
            
        with st.expander("⚙️ Model Numarası (Model Number) Ekle / Düzenle / Kaldır"):
            st.markdown("**1. Yeni Model Numarası Ekle veya Listeyi Düzenle:**")
            new_models_str = st.text_input("Model Numaraları Listesi (Virgülle ayırın)", value=", ".join(saved_models), key="input_saved_models")
            if st.button("Listeyi Kaydet", key="btn_save_models"):
                cleaned_str = ", ".join([c.strip().upper() for c in new_models_str.split(",") if c.strip()])
                save_imap_settings({"SAVED_FDA_MODEL_NUMBERS": cleaned_str})
                st.success(f"Model numaraları listesi güncellendi: {cleaned_str}")
                st.rerun()
                
            st.divider()
            st.markdown("**2. Bir Model Numarasını Tamamen Kaldır:**")
            if saved_models:
                selected_to_remove = st.selectbox("Kaldırılacak Model Numarasını Seçin", saved_models, key="select_model_remove")
                remove_existing_data = st.checkbox("Bu model numarasına ait veritabanındaki mevcut şikâyet kayıtlarını da sil", value=True)
                
                if st.button("Seçilen Model Numarasını Listeden Sil", type="secondary"):
                    updated_list = [c for c in saved_models if c != selected_to_remove]
                    cleaned_str = ", ".join(updated_list)
                    save_imap_settings({"SAVED_FDA_MODEL_NUMBERS": cleaned_str})
                    
                    deleted_count = 0
                    if remove_existing_data:
                        with db() as conn:
                            cur = conn.cursor()
                            cur.execute(
                                "DELETE FROM complaints WHERE UPPER(product) LIKE ? OR UPPER(description) LIKE ? OR UPPER(findings) LIKE ?",
                                (f"%{selected_to_remove.upper()}%", f"%{selected_to_remove.upper()}%", f"%{selected_to_remove.upper()}%")
                            )
                            deleted_count = cur.rowcount
                            conn.commit()
                            
                    st.session_state["fda_sync_msg"] = (
                        "success",
                        f"'{selected_to_remove}' model numarası listeden kaldırıldı! " + (f"Ayrıca veritabanından {deleted_count} adet ilgili şikâyet kaydı silindi." if remove_existing_data else "")
                    )
                    st.rerun()
            else:
                st.info("Listede silinecek model numarası bulunmuyor.")

        if btn_batch_sync:
            if not saved_models:
                st.warning("Lütfen önce en az bir Model Number kaydedin.")
            else:
                total_added = 0
                total_skipped = 0
                details = []
                progress_bar = st.progress(0, text="FDA MAUDE toplu model senkronizasyonu başlatılıyor...")
                
                for idx, model_no in enumerate(saved_models):
                    progress_bar.progress((idx) / len(saved_models), text=f"🔄 [{idx+1}/{len(saved_models)}] Model Number '{model_no}' taranıyor...")
                    added, skipped = fetch_dynamic_fda(
                        brand_name="",
                        model_number=model_no,
                        product_code="",
                        manufacturer="",
                        event_type="Tümü",
                        date_from=date(2020, 1, 1),
                        date_to=date.today(),
                        limit=batch_limit
                    )
                    total_added += added
                    total_skipped += skipped
                    details.append(f"**{model_no}**: +{added} yeni ({skipped} mevcut)")
                
                progress_bar.progress(1.0, text="✅ Tüm model numaraları tarandı!")
                summary_text = " · ".join(details)
                
                if total_added > 0:
                    st.success(f"🎉 Toplu Güncelleme Tamamlandı! Toplam **{total_added} adet YENİ şikâyet** eklendi. ({summary_text})")
                elif total_skipped > 0:
                    st.info(f"✅ Tüm model numaraları kontrol edildi. Taranan **{total_skipped} kaydın tamamı zaten sistemde mevcut**. ({summary_text})")
                else:
                    st.warning(f"⚠️ Kayıtlı model numaraları ({', '.join(saved_models)}) için yeni kayıt bulunamadı veya sunucu yanıt vermedi.")

    st.write("")
    
    with st.form("fda_dynamic_search"):
        col1, col2 = st.columns(2)
        
        with col1:
            model_number = st.text_input("Model Number (Model Numarası)", placeholder="Örn: EM800, MR8-AF02")
            brand_name = st.text_input("Brand Name (Marka Adı)", placeholder="Örn: Midas Rex")
            manufacturer = st.text_input("Manufacturer (Üretici)", placeholder="Örn: Medtronic")
        
        with col2:
            product_code = st.text_input("Product Code (Ürün Kodu)", placeholder="Örn: ERL, HAB vb.")
            event_type = st.selectbox("Event Type (Olay Türü)", ["Tümü", "Ölüm (Death)", "Yaralanma (Injury)", "Arıza (Malfunction)"])
            date_col1, date_col2 = st.columns(2)
            with date_col1:
                date_from = st.date_input("Başlangıç Tarihi", value=date(2020, 1, 1))
            with date_col2:
                date_to = st.date_input("Bitiş Tarihi", value=date.today())
            
        # LİMİT ARTTIRILDI: Maksimum 5000'e çıkarıldı ve 500'er atlıyor
        limit = st.slider("Taranacak Maksimum Kayıt Sayısı (En yeni tarihli olanlar öncelikli taranır)", 10, 5000, 500, step=50)
        
        submitted = st.form_submit_button("🔍 MAUDE'de Ara ve Yeni Kayıtları Senkronize Et", type="primary")
        
    if submitted:
        if not model_number.strip() and not brand_name.strip() and not product_code.strip() and not manufacturer.strip():
            st.warning("Lütfen Model Number, Brand Name, Product Code veya Manufacturer alanlarından en az birini doldurun.")
        else:
            with st.spinner(f"openFDA sunucularına bağlanılıyor. {limit} kayıt taranıyor, bu işlem biraz sürebilir..."):
                added, skipped = fetch_dynamic_fda(
                    brand_name=brand_name.strip(),
                    model_number=model_number.strip(),
                    product_code=product_code.strip(),
                    manufacturer=manufacturer.strip(),
                    event_type=event_type,
                    date_from=date_from,
                    date_to=date.today() if not date_to else date_to,
                    limit=limit
                )
            
            auto_added_info = ""
            if (added > 0 or skipped > 0) and model_number.strip():
                if add_model_to_saved_list(model_number.strip()):
                    auto_added_info = f" 📌 **'{model_number.strip().upper()}'** yukarıdaki takip edilen model numaraları listesine ve Dashboard'a otomatik eklendi!"

            if added > 0:
                st.session_state["fda_sync_msg"] = (
                    "success",
                    f"🎉 İşlem Tamamlandı! **{added} adet YENİ şikâyet** veritabanına aktarıldı. (Zaten sistemde olan {skipped} kayıt atlandı.){auto_added_info}"
                )
                st.rerun()
            elif skipped > 0:
                st.session_state["fda_sync_msg"] = (
                    "info",
                    f"✅ Belirlediğiniz limit dahilinde taranan **{skipped} kaydın tamamı zaten sisteminizde mevcut**. Yeni bir şikâyet bulunamadı.{auto_added_info}"
                )
                st.rerun()
            else:
                st.warning("⚠️ Bu filtrelere uygun hiçbir FDA MAUDE kaydı bulunamadı (Tarih aralığını genişletmeyi veya Model Number yazılımını kontrol etmeyi deneyebilirsiniz).")

    st.divider()
    st.subheader("Gelen şikâyet e-postalarını içe aktarma (IMAP)")
    im = imap_settings()
    with st.form("imap_settings_form"):
        im_host = st.text_input("IMAP sunucusu", value=im.get("host", ""), placeholder="imap.gmail.com")
        im_port = st.text_input("IMAP SSL portu", value=im.get("port", "993"))
        im_user = st.text_input("Gelen kutusu e-posta", value=im.get("username", ""))
        im_pass = st.text_input("Uygulama parolası", value=im.get("password", ""), type="password")
        im_folder = st.text_input("Klasör", value=im.get("folder", "INBOX"))
        im_tag = st.text_input("Konu etiketi", value=im.get("subject_tag", "[SIKAYET]"))
        im_allow = st.text_input("İzinli gönderenler", value=im.get("sender_allowlist", ""))
        im_save = st.form_submit_button("Ayarları kaydet", type="primary")
    if im_save:
        save_imap_settings({
            "IMAP_HOST": im_host.strip(), "IMAP_PORT": im_port.strip() or "993",
            "IMAP_USER": im_user.strip(), "IMAP_PASSWORD": im_pass,
            "IMAP_FOLDER": im_folder.strip() or "INBOX", "IMAP_SUBJECT_TAG": im_tag.strip(),
            "IMAP_SENDER_ALLOWLIST": im_allow.strip()
        })
        st.success("IMAP ayarları kaydedildi.")
    if st.button("Şimdi gelen şikâyetleri kontrol et", type="primary", key="btn_check_imap"):
        with st.spinner("Kontrol ediliyor..."):
            result = poll_inbox(user["username"])
        (st.success if result.get("ok") else st.error)(result.get("message", "İşlem tamamlanamadı."))
        if result.get("imported", 0): 
            st.rerun()