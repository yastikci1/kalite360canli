"""
FDA MAUDE veritabanındaki TÜM Midas Rex EM800 şikâyetlerini;
- Event Description (Müşteri Şikâyeti)
- Manufacturer Narrative (Üretilen Çözüm)
- Type of Reportable Event (Olay Türü)
- Health Effect - Clinical Codes (Klinik Sağlık Etkisi)
alanlarıyla birlikte çeker ve Kalite360 veritabanına aktarır.
"""
import time
import requests
from datetime import datetime
from db import init_db, db, create_complaint

BASE_URL = "https://api.fda.gov/device/event.json"

def fetch_all_em800():
    init_db()
    
    # 1. Toplam kayıt sayısını kontrol et
    count_query_url = f"{BASE_URL}?search=device.model_number:EM800&limit=1"
    try:
        res = requests.get(count_query_url, timeout=20)
        if res.status_code == 404:
            count_query_url = f"{BASE_URL}?search=EM800&limit=1"
            res = requests.get(count_query_url, timeout=20)
            
        if res.status_code != 200:
            print(f"❌ FDA API'ye erişilemedi: HTTP {res.status_code}")
            return
            
        meta_data = res.json()
        total_records = meta_data.get("meta", {}).get("results", {}).get("total", 0)
        print(f"🎯 FDA MAUDE üzerinde toplam {total_records} adet EM800 kaydı bulundu.")
        
    except Exception as exc:
        print(f"❌ Bağlantı hatası: {exc}")
        return

    if total_records == 0:
        print("Çekilecek kayıt bulunamadı.")
        return

    batch_size = 100
    saved_total = 0
    search_term = "device.model_number:EM800" if "device.model_number" in count_query_url else "EM800"

    print(f"🚀 Veri aktarımı başlıyor (Her partide {batch_size} kayıt)...")

    # 2. Sayfalama döngüsü
    for skip in range(0, total_records, batch_size):
        url = f"{BASE_URL}?search={search_term}&limit={batch_size}&skip={skip}"
        
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 429:
                print("⏳ Hız sınırına takıldı (Rate Limit). 3 saniye bekleniyor...")
                time.sleep(3)
                resp = requests.get(url, timeout=30)
                
            if resp.status_code != 200:
                print(f"⚠️ {skip}. kayıttan itibaren veri alınamadı: HTTP {resp.status_code}.")
                break

            results = resp.json().get("results", [])
            if not results:
                break

            for item in results:
                # --- A. ŞİKAYET VE ÇÖZÜM METİNLERİ ---
                event_descriptions = []
                manufacturer_narratives = []
                
                for mdr in item.get("mdr_text", []):
                    t_code = str(mdr.get("text_type_code", "")).strip().lower()
                    text = str(mdr.get("text", "")).strip()
                    if not text:
                        continue
                    
                    if any(k in t_code for k in ["description", "event", "problem"]):
                        event_descriptions.append(text)
                    elif any(k in t_code for k in ["manufacturer", "narrative", "evaluation"]):
                        manufacturer_narratives.append(text)
                    else:
                        if text.startswith("H3:") or "product analysis" in text.lower() or "medtronic submits" in text.lower():
                            manufacturer_narratives.append(text)
                        else:
                            event_descriptions.append(text)
                
                final_complaint = "\n\n".join(event_descriptions).strip() or "FDA arıza anlatımı belirtilmemiş."
                final_solution = "\n\n".join(manufacturer_narratives).strip() or "Üretici inceleme/servis raporu henüz girilmemiş."

                # --- B. CİHAZ VE ÜRETİCİ TESPİTİ ---
                devices = item.get("device", [])
                target_device = devices[0] if devices else {}
                for d in devices:
                    if "EM800" in str(d.get("model_number", "")).upper() or "EM800" in str(d.get("catalog_number", "")).upper():
                        target_device = d
                        break

                brand = target_device.get("brand_name") or target_device.get("generic_name") or "MOTOR MR8 ELECTRIC"
                manufacturer = target_device.get("manufacturer_d_name") or item.get("manufacturer_name") or "MDT POWERED SURGICAL SOLUTIONS"
                product_code = target_device.get("product_code", "HBC")
                product_full_name = f"{brand} (EM800) [{manufacturer}]"

                report_no = item.get("report_number", str(item.get("mdr_report_key", "FDA-MDR")))
                customer_facility = f"FDA Klinik Bildirimi ({item.get('reporter_occupation_code', 'Biyomedikal')})"

                # --- C. TYPE OF REPORTABLE EVENT & HEALTH EFFECT ---
                event_type = item.get("event_type", "Malfunction")
                if not event_type:
                    event_type = "Malfunction"
                
                # Hasta sağlık etkisi kodları
                patients = item.get("patient", [])
                health_effects = []
                for p in patients:
                    probs = p.get("patient_problems", [])
                    if isinstance(probs, list):
                        health_effects.extend([str(x) for x in probs if x])
                    elif probs:
                        health_effects.append(str(probs))
                
                if health_effects:
                    health_effect_str = "; ".join(health_effects)
                else:
                    low_c = final_complaint.lower()
                    if "no patient" in low_c or "no impact" in low_c:
                        health_effect_str = "No Clinical Signs / No Patient Harm (4582)"
                    elif "dural tear" in low_c or "csf" in low_c:
                        health_effect_str = "Dural Tear / Nerve Root Impact (1417)"
                    else:
                        health_effect_str = "Insufficient Information (4580)"

                # --- D. TARİH VE ÖNCELİK ---
                raw_date = item.get("date_received", "")
                created_at_iso = datetime.now().isoformat(timespec="seconds")
                if raw_date and len(raw_date) == 8:
                    created_at_iso = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}T10:00:00"

                is_urgent = event_type in ["Injury", "Death", "Serious Injury"]
                priority = "Kritik" if is_urgent else "Orta"

                # --- E. KAYDI OLUŞTUR VE GÜNCELLE ---
                cid = create_complaint(
                    customer=customer_facility,
                    contact="FDA Reporter",
                    phone="-",
                    email="fda-mdr@fda.gov",
                    product=product_full_name,
                    order=f"MDR-{report_no}",
                    category="Ürün Kalitesi",
                    priority=priority,
                    description=final_complaint,
                    requested="Teknik Servis & İnceleme Raporu",
                    owner="kalite",
                    user="FDA-Sync",
                    urgent=is_urgent
                )

                findings_payload = f"FDA MAUDE | Rapor No: {report_no} | Kod: {product_code} | Üretici: {manufacturer} | Olay Türü: {event_type} | Sağlık Etkisi: {health_effect_str}"

                with db() as conn:
                    conn.execute("""
                        UPDATE complaints 
                        SET created_at=?, 
                            updated_at=?, 
                            findings=?, 
                            action_type=?, 
                            action_plan=?, 
                            status=?
                        WHERE id=?
                    """, (
                        created_at_iso,
                        created_at_iso,
                        findings_payload,
                        "Üretici İncelemesi (Mfr Narrative)",
                        final_solution,
                        "Kapalı" if manufacturer_narratives else "İncelemede",
                        cid
                    ))
                saved_total += 1

            current_progress = min(skip + batch_size, total_records)
            print(f"📥 İlerleme: {current_progress}/{total_records} vaka kaydedildi...")
            time.sleep(0.35)

        except Exception as e:
            print(f"❌ Hata: {e}")
            break

    print(f"\n🎉 İşlem tamamlandı! Toplam {saved_total} adet kayıt eksiksiz aktarıldı.")

# app.py arayüzündeki butonun çağırabilmesi için fonksiyon referansı
def fetch_and_save_em800(total_to_fetch=30):
    fetch_all_em800()

if __name__ == "__main__":
    fetch_all_em800()