# Kalite360 Fabrika Şikâyet Yönetimi — pilot sürüm

## Kurulum
1. Python 3.10+ kurun.
2. Proje klasöründe terminal açın: `python -m venv .venv`
3. Windows: `.venv\\Scripts\\activate` | macOS/Linux: `source .venv/bin/activate`
4. `pip install -r requirements.txt`
5. `streamlit run app.py`

İlk açılışta veritabanı boşsa son 360 güne yayılmış **100 sentetik örnek şikâyet** eklenir. Bunlar gerçek müşteri verisi değildir. Pilot girişleri: `musterihizmetleri / MH2026!`, `kalite / Kalite2026!`, `admin / Admin2026!`.

## Excel aktarımı
Sol menüde **Veri Aktarımı** sayfasına girin. Şikâyet listesinde detay açmak için tablodaki satırı seçin; ayrı detay düğmesi kaldırılmıştır (yeni Streamlit sürümü gerekir). Şablonu indirin, satırları doldurun ve `.xlsx` ya da `.csv` olarak yükleyin. En az `Müşteri/Firma` ve `Açıklama` sütunları gerekir. Diğer sütunlar isteğe bağlıdır. Aktarım mevcut kayıtları silmez. İçe aktarmadan önce önizleme kontrolü yapın.

## Gelen şikâyet e-postaları
SMTP gönderim içindir; gelen postaları almak için IMAP gerekir. **Veri Aktarımı** sayfasında IMAP sunucusu, e-posta adresi, uygulama parolası, klasör ve konu filtresini ayarlayın. Varsayılan filtre `[SIKAYET]` olduğu için örneğin `Konu: [SIKAYET] Mil ölçüsü uygunsuz` mesajı içe aktarılır; kişisel/diğer e-postalar konu filtresini geçmediği için şikâyet kaydı olmaz. İsterseniz izinli gönderen adresi/domain listesi de ekleyebilirsiniz.

- Gmail örneği: `imap.gmail.com`, SSL portu `993`; hesapta IMAP erişimi açık olmalı ve genellikle uygulama parolası gerekir.
- Microsoft 365/Outlook için kurumun IMAP erişimini ve modern kimlik doğrulama gerekliliklerini kontrol edin; bazı kurumsal hesaplarda parola tabanlı IMAP kapalı olabilir.
- `Şimdi gelen şikâyetleri kontrol et` düğmesi filtreye uyan postaları hemen alır. UID/folder kayıtları aynı e-postanın ikinci kez içe aktarılmasını önler. Sadece başarıyla alınan e-posta `Seen` işaretlenir; diğer mailler değiştirilmez.
- **Gmail filtresi:** Gmail arama seçeneklerinden Konu alanına `[SIKAYET]` yazıp filtre oluşturun; `Kalite360-Sikayet` etiketini uygulayın. IMAP klasörüne, Gmail hesabında IMAP üzerinden görünen etiket adını girin. Klasör adları hesap/etiket durumuna göre değişebilir.
- **Otomatik alım:** Proje klasöründe ikinci terminal açıp `python email_worker.py` çalıştırın. İşçi her 180 saniyede bir gelen postaları kontrol eder. Aralığı `POLL_INTERVAL_SECONDS` ortam değişkeniyle değiştirebilirsiniz (en az 60 saniye). İşçi bilgisayar/sunucu açıkken çalışır; bilgisayar kapanırsa durur. Kalıcı sunucuda servis yöneticisiyle çalıştırılmalıdır.
- Gelen e-posta içeriği müşteri verisi içerebilir. Önce şirket politikasını ve veri saklama kurallarını doğrulayın.

## Önemli güvenlik notu
Bu bir yerel pilot/prototiptir. Demo kullanıcıları, parola yönetimi, rol bazlı yetkilendirme, HTTPS, yedekleme, denetim ve kurumsal e-posta OAuth/modern kimlik doğrulaması üretim öncesi sertleştirilmelidir. `.env` dosyasını paylaşmayın veya Git'e yüklemeyin. IMAP alımı otomatik yanıt göndermez.


## Gmail kaynaklı şikâyet renkleri
- Gmail/IMAP ile içe alınan kayıtların Kaynak sütununda `E-posta` bilgisi görünür.
- Detay ekranı ilk kez açılana kadar satır sarı renkte gösterilir.
- Detay ekranına girildikten sonra satır açık yeşile döner.
- Renk durumu SQLite veritabanındaki `email_ingest.viewed_at` alanında saklanır; uygulama yeniden başlatılınca kaybolmaz.
- Manuel ve Excel'den eklenen kayıtlar bu e-posta renk kodlamasına dahil değildir.
