# Kalite360 arıza sınıflandırması güncellemesi

## Kurulum

1. ZIP içindeki dört Python dosyasını projenin ana klasörüne kopyalayın:
   `failure_classifier.py`, `ai_quality_engine.py`, `ai_response_drafter.py`, `ai_agent_engine.py`.
2. ZIP içindeki `views` klasörünün üç dosyasını projenizdeki `views` klasörüne kopyalayın; dosyalar sorulduğunda üzerine yazın.
3. Uygulamayı yeniden başlatın: `streamlit run app.py`.

Bu paket yedi Python dosyası içerir. `failure_classifier.py` yeni ortak modüldür; diğer altı dosya mevcut dosyaların güncel sürümleridir. Ek paket kurulumu veya veri taşıma işlemi gerekmez. Mevcut şikâyetler ekran açıldığında ortak kurallarla sınıflandırılır. Veritabanındaki serbest metin `root_cause` alanına otomatik etiket yazılmaz.

## Ekranda kullanım

- **Dashboard:** Ana arıza grubu filtresini ve alt arıza/ana grup dağılımını seçin.
- **Birincil arıza:** Her vaka, öncelikli tek bir arıza türünde sayılır.
- **Tüm bulgular:** Aynı vakadaki diğer bulgular da gösterilir. Aynı tür, bir vaka için bir kez sayılır. Birden çok bulgu içeren vakalar nedeniyle çubukların toplamı vaka sayısını aşabilir.
- **Şikâyet Listesi:** Ana grup ve alt arıza filtreleri birincil ve ek bulgularda arama yapar. Excel çıktısı bu alanları ve metin dayanağını içerir.
- **Şikâyet Detayı:** Ana grup, birincil alt arıza, ek bulgular, kaynak ve rapordaki neden açıklaması gösterilir. “Sınıflandırma dayanaklarını göster” bölümünden eşleşen metinler okunabilir.
- **Tarihçe ve Benzerlik:** Eşleşmeler daha ayrıntılı alt arıza türü üzerinden hesaplanır.
- **Kalite araçları ve asistan:** Aynı sınıflandırma modülünü kullanır.

Menü halkaları ve renkli detay tasarımı önceki `app.py` dosyanızda devam eder. Detaydaki üç havuz başlığının 25 px ve kırmızımsı renk ayarı bu sürümde korunmuştur.

## Ana gruplar ve alt türler

Belirsiz kayıtlar için ayrı bir sınıflandırılamayan grubu vardır. Dokuz ana arıza grubunda toplam 41 alt tür tanımlıdır:

| Ana grup | Alt tür örnekleri |
|---|---|
| Rulman sistemi | Yerinden ayrılma, kırılma/dağılma, aşınma, korozyon, sıkışma, hizalama/yataklama, türü belirtilmemiş hasar |
| Uç ve kilitleme | Uç takılamaması, çıkarılamaması, kilit tutmaması, pens hasarı, aksesuar oturma uyumsuzluğu |
| Tüp ve hareket mekanizması | Tüp uzatma/geri çekme sorunu, ayar halkası sertliği |
| Mil ve dişli sistemi | Aşınma, kırılma, korozyon |
| Gövde ve yapısal hasar | Deformasyon, gövde kırığı/çatlağı, uç/gövde aşınması, kesici uç kırılması, parça gevşemesi, tolerans uygunsuzluğu |
| Termal sorunlar | Aşırı ısınma, ısıl hasar/yanma bulgusu |
| Elektrik ve bağlantı | Kablo hasarı, temassızlık, izolasyon/kaçak, elektronik/sargı, duman/kıvılcım/kısa devre |
| Fonksiyon ve hareket | Çalışmama/durma, güç/devir kaybı, ses, titreşim/salgı |
| Yüzey ve sızdırmazlık | Gövde korozyonu, diğer oksitlenme, işaret/renk halkası silinmesi, kirlenme, sıvı/hava kaçağı |

## Sınıflandırma mantığı ve sınırları

Bu sürüm yerel, kural tabanlı bir metin sınıflandırıcısı kullanır. Parça ile arıza ifadesi birlikte aranır; sadece “bearing”, “service” veya “temperature” sözcüğünün geçmesi arıza atamak için yeterli değildir. Örneğin “detached bearings” rulman yerinden ayrılması olarak sınıflandırılır. “No overheating” veya başarılı sıcaklık/hipot testleri pozitif arıza bulgusu oluşturmaz.

Üretici/teknik servis raporundaki öncelikli bulgulara, varsa kayıtlı neden açıklamasına ve müşteri bildirimine kaynak önceliği uygulanır. “Ayrıca not edildi” türündeki ek bulgular saklanır. Özgün İngilizce metin varsa öncelikle o kullanılır; yoksa Türkçe çeviri alanına bakılır. Metin açık bir alt türü desteklemiyorsa belirsiz sonuç döndürülür.

**Arıza türü, kesin kök neden kanıtı değildir.** Raporun “olası neden” açıklaması ayrı gösterilir. Üretici nedeni belirleyemediğini yazmışsa durum bu şekilde belirtilir. Kurallar, farklı yazımlar veya karmaşık olumsuzluklar içeren metinlerde hata yapabilir; metin dayanakları teknik incelemeyle değerlendirilebilir. Dağılımlar, görünümü çeşitlendirmek için dengelenmez.

Pareto grafiğindeki %80 çizgisi bir referanstır; 80/20 oranının veri için sağlandığı varsayılmaz. “Tüm bulgular” modunda kümülatif oran bulgu toplamına göre hesaplanır.

## Kontrol edilen örnek

Paylaşılan 2.794 kayıt üzerinde sınıflandırma çalıştırıldı. MR8-AVA15 modelinin 271 kaydında önceki kural 214 vakayı “Fiziksel Kırılma / Çatlak” altında topluyordu. Yeni kurallar, bu modelde birincil etiketi 200 vakada “Rulman yerinden ayrılması” olarak belirledi; üretici raporlarında bu bulgu yaygın olduğu için yüksek kalır.

“Tüm bulgular” kapsamından örnekler: rulman ayrılması 242, rulman aşınması 49, mil/dişli aşınması 43, uç/gövde aşınması 38, uç takılamaması 27, tüp hareketi sorunu 22 vaka. Bu sayılar aynı vakayı farklı bulgularda içerebilir; doğruluk değerlendirmesi veya birbirinden bağımsız vaka toplamları değildir. Yeni veriler eklendikçe sayılar değişir.

Parça/arıza eşleşmesi, olumsuz ifadeler, kaynak önceliği, tekrar sayımı, belirsiz metinler, ek bulgu filtreleri ve boş sonuçlar için 11 test geçti. Dashboard seçenekleri, liste filtresi, detay/PDF üretimi ve kalite araçları ayrıca ağ erişimi kapalı bir kopyada kontrol edildi.

Dashboard'daki model seçimi artık yalnızca görünümü filtreler; ekran açılışındaki otomatik kayıt silme çağrısı kaldırılmıştır.
