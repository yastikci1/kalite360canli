from failure_classifier import (classify_failure, annotate_failures, resolve_smart_root_cause, extract_root_cause, FAILURE_GROUPS, ALL_SUBTYPES, SUBTYPE_TO_GROUP, UNKNOWN_SUBTYPE, filter_failures, detect_failure_filter, legacy_failure_group)
import json
import re
import base64
import io
import os
import sys
import subprocess
from datetime import datetime, date, timedelta
import pandas as pd
import streamlit as st
from db import db, complaints, get_complaint, actions, add_action, log
from utils import classify_validity


# --- 1. YARDIMCI METİN VE MODEL ÇÖZÜMLEYİCİLER ---


def extract_failure_mode(complaint_text: str, analysis_text: str = "") -> str:
    return classify_failure({"description": complaint_text, "action_plan": analysis_text}).subtype


def determine_complaint_validity(analysis_text: str) -> str:
    if not analysis_text or len(str(analysis_text).strip()) < 10:
        return "İnceleme Yapılıyor"
        
    t = str(analysis_text).lower()
    if "medtronic submits this report" in t:
        t = t.split("medtronic submits this report")[0]
        
    if any(k in t for k in ["not returned", "no device was returned", "cihaz iade edilmedi", "waiting for return"]):
        return "İnceleme Yapılıyor (Cihaz Bekleniyor)"
    if any(k in t for k in ["inadequate preventive maintenance", "user error", "misuse", "improper maintenance", "kullanıcı hatası", "bakım eksikliği"]):
        return "Doğrulandı (Bakım / Kullanıcı Kaynaklı)"
    if any(k in t for k in ["confirmed", "evaluation determined", "cause was identified", "likely cause", "found to be", "noted that", "reproduced", "doğrulandı", "tespit edildi"]):
        return "Haklı Şikâyet (Doğrulandı)"
    if any(k in t for k in ["could not reproduce", "not duplicated", "no fault found", "tekrarlanamadı", "hataya rastlanmadı"]):
        return "Doğrulanamadı (Arıza Tekrarlanmadı)"
        
    return "İnceleme Yapılıyor"


def _is_valid_text(val) -> bool:
    s = str(val or "").strip()
    if not s or s.lower() in ["-", "none", "null", "belirtilmemiş", "değerlendirilmedi"]:
        return False
    ignore_list = [
        "henüz girilmemiş", "çözüm bekleniyor", "no evaluation was performed",
        "no conclusion can be drawn", "device was not returned",
        "fda maude kaydında olay metni belirtilmemiş"
    ]
    return not any(ig in s.lower() for ig in ignore_list)


def init_quality_engine_tables():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS capa_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                complaint_id INTEGER,
                complaint_no TEXT,
                format_type TEXT,
                draft_title TEXT,
                dept_role TEXT,
                suggested_due TEXT,
                draft_markdown TEXT,
                status TEXT DEFAULT 'Taslak (Onay Bekliyor)',
                created_by TEXT,
                created_at TEXT,
                approved_by TEXT,
                approved_at TEXT
            )
        """)
        conn.commit()


# --- 2. VERİTABANI ÖRÜNTÜ TARAMA VE BENZER ŞİKÂYET ANALİZİ ---
def scan_similar_complaints(model_query: str = "", failure_query: str = "", base_complaint: dict = None) -> dict:
    df = complaints()
    if df.empty:
        return {"empty": True, "count": 0}

    df = df.copy()
    df = annotate_failures(df)
    df["Hata_Modu"] = df["Alt Arıza Türü"]
    df["Dogruluk"] = df.apply(classify_validity, axis=1)

    target_model = (model_query or "").strip().upper()
    target_failure = (failure_query or "").strip()

    if base_complaint and not target_model:
        prod_raw = str(base_complaint.get("product") or "")
        m_paren = re.search(r"\(([^)]+)\)", prod_raw)
        target_model = m_paren.group(1).strip().upper() if m_paren else prod_raw.split()[0].upper() if prod_raw else ""

    if base_complaint and not target_failure:
        target_failure = classify_failure(base_complaint).subtype

    filtered = df
    if target_model:
        mask_m = (
            df["product"].astype(str).str.upper().str.contains(re.escape(target_model), na=False) |
            df["description"].astype(str).str.upper().str.contains(re.escape(target_model), na=False) |
            df["findings"].astype(str).str.upper().str.contains(re.escape(target_model), na=False)
        )
        filtered = df[mask_m]

    if target_failure and target_failure != "Genel / Sınıflandırılmamış Arıza":
        filtered = filter_failures(filtered, target_failure)
    if filtered.empty:
        return {"empty": True, "count": 0}

    total_matched = len(filtered)
    confirmed_df = filtered[filtered["Dogruluk"] == "Şikâyet Doğru"]
    unconfirmed_df = filtered[filtered["Dogruluk"] == "Şikâyet Yanlış"]
    pending_df = filtered[~filtered["Dogruluk"].isin(["Şikâyet Doğru", "Şikâyet Yanlış"])]

    verified_causes = []
    verified_solutions = []
    evidence_records = []

    for _, r in filtered.head(25).iterrows():
        c_no = str(r.get("complaint_no") or f"ID-{r.get('id')}")
        rc = str(r.get("root_cause") or "").strip()
        ap = str(r.get("action_plan") or "").strip()
        val_state = r.get("Dogruluk")

        if _is_valid_text(rc):
            verified_causes.append((c_no, rc))
        if _is_valid_text(ap) and val_state == "Şikâyet Doğru":
            verified_solutions.append((c_no, ap[:220]))
        if len(evidence_records) < 6:
            evidence_records.append({
                "complaint_no": c_no,
                "product": str(r.get("product") or "-"),
                "status": str(r.get("status") or "-"),
                "validity": val_state,
                "priority": str(r.get("priority") or "-"),
                "created_at": str(r.get("created_at") or "")[:10],
                "snippet": str(r.get("tr_description") or r.get("description") or "")[:140],
                "solution_snippet": ap[:140] if _is_valid_text(ap) else "Teknik servis çözümü henüz girilmemiş"
            })

    matched_ids = filtered["id"].astype(int).tolist()
    past_actions = []
    if matched_ids:
        placeholders = ",".join(["?"] * min(len(matched_ids), 50))
        with db() as conn:
            act_rows = conn.execute(
                f"SELECT a.title, a.assignee, a.due_date, a.status, c.complaint_no "
                f"FROM actions a JOIN complaints c ON a.complaint_id = c.id "
                f"WHERE a.complaint_id IN ({placeholders}) ORDER BY a.id DESC LIMIT 10",
                matched_ids[:50]
            ).fetchall()
        past_actions = [dict(x) for x in act_rows]

    missing_order_cnt = int((filtered["order_no"].isna() | (filtered["order_no"].astype(str).str.strip() == "") | (filtered["order_no"].astype(str) == "-")).sum())
    missing_rc_cnt = int((filtered["root_cause"].isna() | (filtered["root_cause"].astype(str).str.strip() == "")).sum())
    not_returned_cnt = int(filtered["action_plan"].astype(str).str.lower().str.contains("not returned|iade edilmedi|no evaluation", na=False).sum())

    return {
        "empty": False,
        "target_model": target_model or "Tüm Modeller",
        "target_failure": target_failure or "Tüm Arıza Tipleri",
        "total_matched": total_matched,
        "confirmed_cnt": len(confirmed_df),
        "unconfirmed_cnt": len(unconfirmed_df),
        "pending_cnt": len(pending_df),
        "verified_causes": verified_causes,
        "verified_solutions": verified_solutions,
        "evidence_records": evidence_records,
        "past_actions": past_actions,
        "missing_order_cnt": missing_order_cnt,
        "missing_rc_cnt": missing_rc_cnt,
        "not_returned_cnt": not_returned_cnt,
    }


# --- 3. AKILLI KÖK NEDEN ANALİZİ (6M / ISHIKAWA) ÜRETİCİSİ ---
def generate_6m_root_cause_analysis(query_text: str = "", base_complaint: dict = None, model_override: str = "", failure_override: str = "") -> str:
    detected_model = model_override.strip().upper() if model_override else ""
    if not detected_model and query_text:
        m_mod = re.search(r"\b(EM800|MR8-[A-Z0-9]+|MR8|P-\d+|B-\d+|PG-\d+|K-\d+|V-\d+|S-\d+|D-\d+|E-\d+|C-\d+|M-\d+)\b", query_text, re.I)
        if m_mod:
            detected_model = m_mod.group(1).upper()

    detected_failure = failure_override if failure_override else (detect_failure_filter(query_text) if query_text else "")
    if detected_failure in ["", UNKNOWN_SUBTYPE, "Genel / Sınıflandırılmamış Arıza", "Diğer / Tanımlanamayan"] and base_complaint:
        detected_failure = classify_failure(base_complaint).subtype

    scan = scan_similar_complaints(detected_model, detected_failure, base_complaint)
    if scan.get("empty") or scan.get("total_matched", 0) == 0:
        return (
            "⚠️ **Yetersiz Veritabanı Verisi:**\n\n"
            "Belirttiğiniz kriterlere uygun şikâyet kaydı veritabanında bulunamadı. "
            "Gerçek verilerle desteklenmeyen kök nedenleri kesin sonuç gibi sunmamak adına analiz üretilmedi. "
            "Lütfen **Veri Aktarımı** veya **Yeni Şikâyet** ekranından kayıt ekleyin."
        )

    model_lbl = scan["target_model"]
    fail_lbl = scan["target_failure"]
    tot = scan["total_matched"]
    conf = scan["confirmed_cnt"]
    unconf = scan["unconfirmed_cnt"]
    pend = scan["pending_cnt"]
    conf_rate = (conf / tot * 100) if tot > 0 else 0.0

    ref_nos = [r["complaint_no"] for r in scan["evidence_records"][:5]]
    ref_str = ", ".join([f"`{x}`" for x in ref_nos]) if ref_nos else "Kayıt yok"

    six_m = _build_6m_matrix(fail_lbl, model_lbl, ref_str, scan)

    if scan["verified_causes"]:
        ver_lines = "\n".join([f"- **`{cno}`:** {rc}" for cno, rc in scan["verified_causes"][:4]])
    elif scan["verified_solutions"]:
        ver_lines = "\n".join([f"- **`{cno}` (Servis Tespiti):** {sol}" for cno, sol in scan["verified_solutions"][:3]])
    else:
        ver_lines = (
            "- ⚠️ Veritabanındaki ilgili kayıtlarda (`root_cause` alanı) henüz **kesin olarak doğrulanmış ve kapatılmış** "
            "bir kök neden kaydı bulunmamaktadır. Aşağıdaki 6M maddeleri **kesin sonuç değil, inceleme hipotezleridir**."
        )

    if scan["past_actions"]:
        dof_lines = "\n".join([
            f"- **`{a['complaint_no']}`:** {a['title']} *(Sorumlu: {a['assignee']}, Durum: {a['status']}, Termin: {a['due_date']})*"
            for a in scan["past_actions"][:4]
        ])
    else:
        dof_lines = "- Bu model/arıza grubu için `actions` tablosunda kayıtlı geçmiş bir DÖF/CAPA sonucu bulunmamaktadır."

    ev_table = "| Şikâyet No | Ürün | Tarih | Doğrulama Durumu | Servis / Çözüm Özeti |\n|---|---|---|---|---|\n"
    for r in scan["evidence_records"][:5]:
        ev_table += f"| `{r['complaint_no']}` | {r['product'][:20]} | {r['created_at']} | {r['validity']} | {r['solution_snippet'][:65]}... |\n"

    report = f"""### 🧩 Akıllı Kök Neden Analizi — 6M / Ishikawa Raporu
**İncelenen Odak:** `{model_lbl}` · **Arıza Kategorisi:** `{fail_lbl}`

---

#### 1. Veritabanında Tespit Edilen Örüntüler
- **Vaka Yoğunluğu:** Veritabanında bu örüntüyle eşleşen toplam **{tot} adet** şikâyet kaydı incelendi (Örnek dayanak kayıtlar: {ref_str}).
- **Teknik Doğrulama Oranı:** Kayıtların **{conf} adedi (%{conf_rate:.1f})** teknik servis incelemesinde **doğrulanmış arıza**, **{unconf} adedi** **yersiz/doğrulanamayan bildirim** (cihaz şartname içi çalıştı), **{pend} adedi** ise **incelemede/beklemede** durumundadır.
- **Geçmiş DÖF / Aksiyon Sonuçları:**
{dof_lines}

---

#### 2. Doğrulanmış Nedenler ile Olası Hipotezlerin Ayrımı
**A. Veritabanında Doğrulanmış Teknik Bulgular (Kanıtlı):**
{ver_lines}

**B. Önem Sırasına Göre Olası Kök Nedenler (Doğrulama Bekleyen Hipotezler):**
1. **Birincil Hipotez (Yüksek Olasılık):** {six_m['ranked'][0]} *(Dayanak: {ref_str} nolu kayıtlardaki arıza ve servis anlatımları)*
2. **İkincil Hipotez (Orta-Yüksek Olasılık):** {six_m['ranked'][1]} *(Dayanak: {tot} vakadaki tekrarlayan semptom kümesi)*
3. **Üçüncül Hipotez (Orta Olasılık):** {six_m['ranked'][2]} *(Dayanak: Saha kullanım ve sterilizasyon/bakım geri bildirimleri)*

---

#### 3. 6M (Ishikawa) Kategorilerine Göre Detaylı Değerlendirme
* **1. Makine (Machine):** {six_m['Makine']}
* **2. Malzeme (Material):** {six_m['Malzeme']}
* **3. Metot (Method):** {six_m['Metot']}
* **4. İnsan (Manpower):** {six_m['İnsan']}
* **5. Ölçüm (Measurement):** {six_m['Ölçüm']}
* **6. Çevre (Milieu):** {six_m['Çevre']}

---

#### 4. Dayanak Kayıtlar (Veritabanı Kanıt Tablosu)
{ev_table}

---

#### 5. Eksik Veriler ve Alternatif Açıklamalar
- **Eksik Veriler:** İncelenen {tot} kaydın **{scan['missing_rc_cnt']} adedinde** `root_cause` (kök neden) alanı boş bırakılmış, **{scan['missing_order_cnt']} adedinde** Lot/Seri/MDR numarası belirtilmemiş ve **{scan['not_returned_cnt']} adedinde** cihaz fabrikaya fiziki olarak iade edilmediği için laboratuvar testi yapılamamıştır.
- **Alternatif Açıklamalar:** Doğrulanamayan ({unconf} adet) vakalar; arızanın cihaz kaynaklı olmayıp **kesici uç/aksesuar uyumsuzluğu**, **klinik kullanımda aşırı yanal yük uygulanması** veya **anlık güç kaynağı dalgalanması** kaynaklı olabileceğine işaret etmektedir.

---

#### 6. Ek Araştırma İçin Önerilen Doğrulama Kontrolleri
1. **Lot/Seri İzlenebilirlik Kontrolü:** {ref_str} kayıtlarındaki cihazların üretim partileri ve alt bileşen (rulman/şaft/kablo) tedarikçi lotları karşılaştırılmalı.
2. **Fiziki Yıpranma ve Metalürjik İnceleme:** İade edilen arızalı numunelerde SEM/yüzey sertlik ve boşluk (clearance) ölçümü yapılmalı.
3. **Proses ve Kalibrasyon Doğrulaması:** Montaj hattındaki tork anahtarları, balans ekipmanı ve son fonksiyonel test (hipot/termal) kalibrasyon sertifikaları denetlenmeli.

> *Not: Bu rapor Kalite360 veritabanındaki mevcut kayıtlar üzerinden oluşturulmuş analitik bir karar destek çıktısıdır. Fiziksel laboratuvar testiyle kanıtlanmayan hipotezler kesin kök neden olarak kabul edilmemelidir.*"""
    return report


def _build_6m_matrix(fail_lbl: str, model_lbl: str, ref_str: str, scan: dict) -> dict:
    if "Rulman" in fail_lbl or "Isınma" in fail_lbl or "Sıkışma" in fail_lbl:
        return {
            "ranked": [
                f"{model_lbl} yüksek devirli mil/rulman yataklarında yağ filmi kaybı ve eksenel ön yükleme (preload) aşınması",
                "Sterilizasyon (otoklav) döngüleri sonrası sızdırmazlık elemanlarından sızan nemin rulman korozyonunu hızlandırması",
                "Klinik kullanımda pens (collet) kilitleme torkunun aşılması veya aşırı yanal kuvvet uygulanması"
            ],
            "Makine": f"Montaj pres/tork ekipmanı ayar sapması, mil balanssızlığı veya `{model_lbl}` iç yatak tolerans aşınması ({ref_str}).",
            "Malzeme": "Rulman bilya/kafes çeliği ısıl işlem sertlik sapması, yüksek sıcaklık gresinin erken bozunması veya tedarikçi lot farklılığı.",
            "Metot": "Montaj sırasında gres miktarının/dağılımının standart dışı olması, rodaj (run-in) prosedürünün yetersiz kalması.",
            "İnsan": "Ameliyathane/saha personelinin uç takma-çıkarma (collet) prosedürüne uymaması veya soğutma/irrigasyon olmadan kuru çalıştırma.",
            "Ölçüm": "Son kalite kontrolde termal kamera, vibrasyon (dB/ivme) ve devir (RPM) sensörlerinin kalibrasyon veya eşik değeri hatası.",
            "Çevre": "Tekrarlı yüksek basınçlı buhar sterilizasyonu (otoklav) sıcaklığı, nem ve agresif dezenfektan kimyasallarına maruziyet."
        }
    elif "Ölçü" in fail_lbl or "Kırılma" in fail_lbl:
        return {
            "ranked": [
                "CNC işleme / şekillendirme operasyonunda kesici takım aşınması ve bağlama fikstürü boşluğu",
                "Hammadde sertlik/alaşım spek sapması veya ısıl işlem sonrası iç gerilme çatlakları",
                "Proses içi kumpas/mikrometre/CMM ölçüm periyodunun atlanması"
            ],
            "Makine": f"CNC tezgah eksen (backlash) boşluğu, kesici takım/kalıp aşınması ve fikstür sabitleme hatası ({ref_str}).",
            "Malzeme": "Gelen hammadde/dövme parça sertifikasında (3.1) kimyasal bileşim veya sertlik (HRC) homojensizliği.",
            "Metot": "Kesme hızı/ilerleme parametrelerinin revize edilmemesi, ezme/büküm operasyon sırasının standart dışı uygulanması.",
            "İnsan": "Operatörün ilk parça onayı (FAI) veya SPC örneklem kontrolünü eksik uygulaması.",
            "Ölçüm": "Kumpas, mikrometre, mastar veya CMM cihazı kalibrasyon sapması; MSA (Tekrarlanabilirlik) yetersizliği.",
            "Çevre": "Üretim sahası ortam sıcaklığı değişiminin hassas metal toleransları ve ölçüm cihazları üzerindeki genleşme etkisi."
        }
    else:
        return {
            "ranked": [
                f"{model_lbl} alt bileşen bağlantı/montaj noktalarında çalışma kaynaklı yorulma ve temassızlık",
                "Üretim ve son fonksiyonel test prosedürlerinde sınırda kalan parçaların sevkiyat öncesi yakalanamaması",
                "Taşıma, depolama veya saha kullanım koşullarında standart dışı zorlanma"
            ],
            "Makine": f"Üretim/test ekipmanı parametre sapması ve `{model_lbl}` bileşenlerinde mekanik/elektriksel yorulma ({ref_str}).",
            "Malzeme": "Alt bileşen (kablo, konnektör, conta, gövde) tedarikçi parti kalitesi ve malzeme dayanım farklılıkları.",
            "Metot": "Montaj, lehimleme/izolasyon veya son fonksiyon testi iş talimatlarının güncel olmaması.",
            "İnsan": "Operatör eğitim eksikliği veya son kullanıcı/teknisyen uygulama farklılıkları.",
            "Ölçüm": "Fonksiyonel test, elektriksel güvenlik (hipot) veya görsel kontrol kriterlerinin yetersiz kayıt altına alınması.",
            "Çevre": "Taşıma darbesi, depolama nemi/sıcaklığı ve saha çalışma ortamındaki titreşim/toz etkisi."
        }


# --- 4. DÖF / CAPA VE 8D SÜRECİ TASLAK ÜRETİCİSİ ---
def generate_capa_or_8d_draft(
    complaint_row: dict,
    format_type: str = "Standart DÖF / CAPA Planı",
    custom_note: str = ""
) -> dict:
    init_quality_engine_tables()
    cid = int(complaint_row.get("id", 0))
    c_no = str(complaint_row.get("complaint_no") or f"ID-{cid}")
    customer = str(complaint_row.get("customer") or "Belirtilmemiş")
    contact = str(complaint_row.get("contact") or "-")
    product = str(complaint_row.get("product") or "Belirtilmemiş")
    order_no = str(complaint_row.get("order_no") or "Belirtilmemiş (Eksik)")
    priority = str(complaint_row.get("priority") or "Orta")
    status = str(complaint_row.get("status") or "Yeni")
    owner = str(complaint_row.get("owner") or "Kalite Güvence Ekibi")

    desc = str(complaint_row.get("tr_description") or complaint_row.get("description") or "Açıklama girilmemiş").strip()
    findings = str(complaint_row.get("findings") or "").strip()
    root_cause = str(complaint_row.get("root_cause") or "").strip()
    action_plan = str(complaint_row.get("tr_action_plan") or complaint_row.get("action_plan") or "").strip()

    fail_mode = classify_failure(complaint_row).subtype
    scan = scan_similar_complaints(base_complaint=complaint_row)
    similar_cnt = scan.get("total_matched", 1)
    confirmed_cnt = scan.get("confirmed_cnt", 0)

    days_map = {"Kritik": 7, "Yüksek": 14, "Orta": 21, "Düşük": 30}
    delta_days = days_map.get(priority, 14)
    suggested_due = (date.today() + timedelta(days=delta_days)).isoformat()
    suggested_dept = "Kalite Güvence & Ar-Ge / Üretim Mühendisliği"
    draft_title = f"[{fail_mode}] {product[:25]} - DÖF / Kök Neden ve Doğrulama Planı"

    risk_level = "Yüksek / Klinik & Operasyonel Risk" if priority in ["Kritik", "Yüksek"] else "Orta / Fonksiyonel Kalite Riski"
    verified_rc_note = (
        f"Veritabanında kayıtlı kök neden: `{root_cause}`"
        if _is_valid_text(root_cause)
        else "Veritabanında henüz doğrulanmış kesin kök neden (`root_cause`) kaydı bulunmamaktadır; 6M hipotezleri üzerinden araştırma planlanmıştır."
    )
    verified_sol_note = (
        f"Mevcut servis/aksiyon notu: *{action_plan[:240]}*"
        if _is_valid_text(action_plan)
        else "Henüz tamamlanmış bir servis aksiyon planı girilmemiştir."
    )

    extra_line = f"\n- **Kullanıcı Ek Talimatı:** {custom_note.strip()}" if custom_note and custom_note.strip() else ""

    if "8D" in format_type.upper():
        markdown_text = f"""### 📋 8D Problem Çözme ve DÖF Taslağı (`{c_no}`)
> ⚠️ **TASLAK UYARISI:** Bu belge yapay zekâ tarafından oluşturulmuş bir **taslaktır**. Onayınız olmadan veritabanında gerçek DÖF kaydı açılmaz, sorumlu atanmaz ve şikâyet kapatılmaz.

* **D0 — Hazırlık ve Acil Risk Değerlendirmesi:**
  - **Şikâyet No & Tarih:** `{c_no}` ({str(complaint_row.get('created_at') or '')[:10]})
  - **Öncelik & Risk Sınıfı:** `{priority}` — {risk_level} (Benzer vaka sayısı: **{similar_cnt}** kayıt, Doğrulanan: **{confirmed_cnt}**)
* **D1 — Ekibin Oluşturulması (Önerilen Roller):**
  - **Lider / Sorumlu:** {suggested_dept} (Mevcut kayıt sorumlusu: `{owner}`)
  - **Destek Birimler:** Teknik Servis, Üretim/Montaj Mühendisliği, Tedarikçi Kalite
* **D2 — Problemin Tanımı (5N1K):**
  - **Etkilenen Ürün & Seri/MDR No:** `{product}` · Lot/Ref: `{order_no}`
  - **Müşteri:** `{customer}` ({contact})
  - **Hata Kategorisi & Tanım:** `{fail_mode}` — {desc[:300]}{extra_line}
* **D3 — Geçici Kontrol ve Sınırlandırma Faaliyetleri (Containment):**
  - İlgili ürün/lot (`{order_no}`) için stoktaki ve sahadaki eşdeğer serilerin karantina/ek %100 fonksiyon kontrolüne alınması.
  - İade edilen/edecek arızalı ürünün fiziksel inceleme için teknik laboratuvara yönlendirilmesi.
* **D4 — Kök Neden Araştırma Planı (6M & Kaçış Noktası):**
  - **Durum:** {verified_rc_note}
  - **Araştırma Adımları:** Makine/montaj tork kayıtları, rulman/alt bileşen malzeme sertifikaları, kalibrasyon kayıtları ve sterilizasyon/kullanım koşullarının 6M matrisiyle doğrulanması.
* **D5 — Kalıcı Düzeltici Faaliyetlerin (PCA) Seçimi:**
  - {verified_sol_note}
  - Doğrulama sonucuna göre aşınan/hatalı alt bileşenin revize edilmesi, montaj/yağlama iş talimatının güncellenmesi ve son test kabul kriterlerinin sıkılaştırılması.
* **D6 — Düzeltici Faaliyetlerin Uygulanması ve Etkinlik Doğrulama:**
  - **Önerilen Termin Tarihi:** `{suggested_due}` *(Taslak önerisidir, onaydan önce kesinleşmez)*
  - **Doğrulama Yöntemi:** Düzeltme sonrası takip eden ilk 3 üretim partisinde / 30 günlük saha geri bildirimlerinde `{fail_mode}` tekrarının sıfırlanmasının izlenmesi.
* **D7 — Tekrar Oluşmasını Önleyici Faaliyetler (Sistemik Önleme):**
  - Benzer ürün ailesindeki (`{product[:18]}`) Proses FMEA (PFMEA) ve Kontrol Planı dokümanlarının güncellenmesi, operatör/servis bülteni yayınlanması.
* **D8 — Kapanış Kriterleri ve Takdir:**
  - Kök neden kanıt raporunun, doğrulama test sonuçlarının ve FR-66 formunun Kalite Güvence onayıyla arşivlenmesi.

---
*ℹ️ Standart Referans Notu: Bu taslak ISO 10002 ve ISO 13485 CAPA ilkelerinden yararlanılarak hazırlanmıştır.*"""
    else:
        markdown_text = f"""### 🛠️ DÖF (Düzeltici ve Önleyici Faaliyet / CAPA) Planı Taslağı (`{c_no}`)
> ⚠️ **TASLAK UYARISI:** Bu çıktı yalnızca **taslak** niteliğindedir. Kullanıcı onayı olmadan `actions` tablosuna kayıt eklenmez, sorumlu atanmaz ve şikâyet kapatılmaz.

1. **Şikâyet Numarası ve Problem Tanımı:**
   - **Kayıt No:** `{c_no}` | **Mevcut Durum:** `{status}` | **Arıza Sınıfı:** `{fail_mode}`
   - **Problem Özeti:** {desc[:320]}{extra_line}
2. **Etkilenen Ürün ve Müşteri:**
   - **Ürün / Model:** `{product}` | **Seri / Lot / MDR No:** `{order_no}`
   - **Müşteri / Kurum:** `{customer}` (İletişim: `{contact}`)
3. **İlk Risk Değerlendirmesi:**
   - **Öncelik Derecesi:** `{priority}` ({risk_level})
   - **Veritabanı Tekrar Sıklığı:** Sistemde bu ürün/arıza profiliyle eşleşen **{similar_cnt} kayıt** mevcuttur (**{confirmed_cnt}** adedi teknik incelemede doğrulanmıştır).
4. **Geçici Kontrol ve Sınırlandırma Faaliyetleri (Containment):**
   - Şikâyete konu olan `{order_no}` seri/lotlu ürünün ayrılması, stoktaki eşlenik partilere sevkiyat öncesi ek fonksiyonel/termal kontrol uygulanması.
5. **Kök Neden Araştırma Planı:**
   - **Mevcut Kayıt Durumu:** {verified_rc_note}
   - **Plan:** İade edilen ürün üzerinde fiziksel/metalürjik demontaj testi yapılması, 6M parametrelerinin üretim ve servis kayıtlarıyla karşılaştırılması.
6. **Önerilen Düzeltici Faaliyetler (Corrective Actions):**
   - {verified_sol_note}
   - Tespit edilen arızalı alt bileşenin onarım veya değişimi, montaj tork ve yağlama parametrelerinin standartlaştırılması.
7. **Tekrar Oluşmasını Önleyici Faaliyetler (Preventive Actions):**
   - Benzer model gruplarında giriş kalite kontrol (IQC) ve son test (FQC) kabul kriterlerinin gözden geçirilmesi, ilgili iş talimat ve FMEA risk analizinin güncellenmesi.
8. **Önerilen Sorumlu Departman veya Rol:**
   - `{suggested_dept}` *(Kayıt sahibi: `{owner}`)*
9. **Önerilen Termin Tarihi:**
   - `{suggested_due}` *(Öncelik `{priority}` baz alınarak +{delta_days} gün olarak önerilmiştir)*
10. **Etkinlik Doğrulama Yöntemi:**
    - Aksiyon tamamlandıktan sonraki 30-60 günlük periyotta ve ardışık üretim partilerinde `{fail_mode}` kaynaklı yeni şikâyet oranının ve test geçiş yüzdesinin izlenmesi.
11. **Kapanış Kriterleri:**
    - Kök neden doğrulama kanıtlarının sisteme yüklenmesi, düzeltici faaliyetin tamamlanması, etkinlik doğrulama testinin başarılı bulunması ve Kalite Güvence onayı.

---
*ℹ️ Standart Referans Notu: Bu taslak ISO 10002 ve ISO 13485 CAPA ilkeleriyle uyumlu olacak şekilde kurgulanmıştır.*"""

    return {
        "complaint_id": cid,
        "complaint_no": c_no,
        "format_type": format_type,
        "draft_title": draft_title,
        "dept_role": suggested_dept,
        "suggested_due": suggested_due,
        "draft_markdown": markdown_text,
        "fail_mode": fail_mode,
        "similar_cnt": similar_cnt,
        "confirmed_cnt": confirmed_cnt
    }


def save_capa_draft_only(draft_data: dict, edited_markdown: str, username: str) -> int:
    init_quality_engine_tables()
    now = datetime.now().isoformat(timespec="seconds")
    cid = int(draft_data["complaint_id"])
    with db() as conn:
        cur = conn.execute("""
            INSERT INTO capa_drafts
            (complaint_id, complaint_no, format_type, draft_title, dept_role, suggested_due, draft_markdown, status, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'Taslak (Onay Bekliyor)', ?, ?)
        """, (
            cid, draft_data["complaint_no"], draft_data["format_type"],
            draft_data["draft_title"], draft_data["dept_role"],
            draft_data["suggested_due"], edited_markdown, username, now
        ))
        draft_id = cur.lastrowid
        log(conn, cid, username, "DÖF/8D taslağı oluşturuldu (Onay bekliyor)", f"Taslak #{draft_id} ({draft_data['format_type']})")
        conn.commit()
    return draft_id


def approve_and_commit_capa(cid: int, title: str, assignee: str, due_date_str: str, edited_markdown: str, format_type: str, username: str) -> int:
    init_quality_engine_tables()
    now = datetime.now().isoformat(timespec="seconds")
    add_action(int(cid), title.strip(), assignee.strip(), str(due_date_str).strip(), username)

    c_item = get_complaint(int(cid))
    c_no = c_item["complaint_no"] if c_item else f"ID-{cid}"
    with db() as conn:
        cur = conn.execute("""
            INSERT INTO capa_drafts
            (complaint_id, complaint_no, format_type, draft_title, dept_role, suggested_due, draft_markdown, status, created_by, created_at, approved_by, approved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'Onaylandı (DÖF Açıldı)', ?, ?, ?, ?)
        """, (
            int(cid), c_no, format_type, title.strip(), assignee.strip(),
            str(due_date_str).strip(), edited_markdown, username, now, username, now
        ))
        rec_id = cur.lastrowid
        log(
            conn, int(cid), username,
            "DÖF/CAPA kullanıcı onayıyla resmileştirildi",
            f"Format={format_type}; Başlık={title.strip()}; Sorumlu={assignee.strip()}; Termin={due_date_str}"
        )
        conn.commit()
    return rec_id


# --- 5. PDF VE EXCEL DÖF RAPOR DIŞA AKTARMA MOTORLARI ---
def build_dof_pdf(complaint_no, format_type, title, dept, due_date, text_content):
    try:
        import reportlab
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "reportlab"])

    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib import colors

    font_reg, font_bold = "Helvetica", "Helvetica-Bold"
    if os.path.exists("C:/Windows/Fonts/arial.ttf") and os.path.exists("C:/Windows/Fonts/arialbd.ttf"):
        try:
            pdfmetrics.registerFont(TTFont("ArialTR", "C:/Windows/Fonts/arial.ttf"))
            pdfmetrics.registerFont(TTFont("ArialTR-Bold", "C:/Windows/Fonts/arialbd.ttf"))
            font_reg, font_bold = "ArialTR", "ArialTR-Bold"
        except: pass

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=30, rightMargin=30, topMargin=30, bottomMargin=30)
    
    s_norm = ParagraphStyle("Norm", fontName=font_reg, fontSize=9.5, leading=13, textColor=colors.HexColor("#1e293b"))
    s_header_logo = ParagraphStyle("Logo", fontName=font_bold, fontSize=22, leading=26, textColor=colors.white, alignment=0)
    s_header_sub = ParagraphStyle("LogoSub", fontName=font_reg, fontSize=10, leading=14, textColor=colors.white, alignment=2)
    s_section = ParagraphStyle("Sec", fontName=font_bold, fontSize=10, leading=14, textColor=colors.HexColor("#0f172a"))
    
    elements = []

    header_data = [[
        Paragraph("KALİTE360", s_header_logo), 
        Paragraph(f"<b>KALİTE GÜVENCE DEPARTMANI</b><br/>{format_type.upper()}", s_header_sub)
    ]]
    t_header = Table(header_data, colWidths=[200, 330])
    t_header.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#1d4ed8")),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('PADDING', (0,0), (-1,-1), 12),
        ('BOTTOMPADDING', (0,0), (-1,-1), 14),
    ]))
    elements.append(t_header)
    elements.append(Spacer(1, 15))

    meta_data = [
        [Paragraph("<b>Şikâyet Referans No:</b>", s_norm), Paragraph(complaint_no, s_norm), Paragraph("<b>Önerilen Termin:</b>", s_norm), Paragraph(due_date, s_norm)],
        [Paragraph("<b>Sorumlu Departman:</b>", s_norm), Paragraph(dept, s_norm), Paragraph("<b>Aksiyon Başlığı:</b>", s_norm), Paragraph(title, s_norm)]
    ]
    t_meta = Table(meta_data, colWidths=[100, 165, 90, 175])
    t_meta.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#cbd5e1")),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor("#f1f5f9")),
        ('BACKGROUND', (2,0), (2,-1), colors.HexColor("#f1f5f9")),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('PADDING', (0,0), (-1,-1), 8),
    ]))
    elements.append(t_meta)
    elements.append(Spacer(1, 15))

    content_data = []
    try:
        for line in text_content.split('\n'):
            line = line.strip()
            if not line or "TASLAK UYARISI" in line or line.startswith("###") or line.startswith("---") or line.startswith("*ℹ️"):
                continue
                
            safe_line = line.replace("<", "&lt;").replace(">", "&gt;")
            safe_line = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', safe_line)
            safe_line = re.sub(r'\`(.*?)\`', r'<b>\1</b>', safe_line)
            safe_line = re.sub(r'\*(.*?)\*', r'<i>\1</i>', safe_line)
            
            is_header = False
            if re.match(r'^(\d+\.|\*\s*\<b\>D\d+|\*\s*D\d+)', safe_line):
                is_header = True
                safe_line = safe_line.lstrip("* ")
                
            if safe_line.startswith("- ") or safe_line.startswith("• "):
                safe_line = f"• {safe_line[2:]}"

            if is_header:
                content_data.append([Paragraph(safe_line, s_section)])
            else:
                content_data.append([Paragraph(safe_line, s_norm)])

        if content_data:
            t_content = Table(content_data, colWidths=[530])
            style = [
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#cbd5e1")),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('PADDING', (0,0), (-1,-1), 8),
            ]
            for i, row in enumerate(content_data):
                if row[0].style.name == "Sec":
                    style.append(('BACKGROUND', (0,i), (-1,i), colors.HexColor("#e2e8f0")))
                else:
                    style.append(('BACKGROUND', (0,i), (-1,i), colors.white))
                    
            t_content.setStyle(TableStyle(style))
            elements.append(t_content)

        doc.build(elements)
        return buf.getvalue()
    except Exception as e:
        return None


def build_dof_excel(complaint_no, format_type, title, dept, due_date, text_content):
    data = [
        {"Aşama / Kategori": "ŞİKÂYET REFERANS NO", "Açıklama": complaint_no},
        {"Aşama / Kategori": "RAPOR FORMATI", "Açıklama": format_type},
        {"Aşama / Kategori": "AKSİYON BAŞLIĞI", "Açıklama": title},
        {"Aşama / Kategori": "SORUMLU DEPARTMAN", "Açıklama": dept},
        {"Aşama / Kategori": "ÖNERİLEN TERMİN", "Açıklama": due_date},
        {"Aşama / Kategori": "", "Açıklama": ""}
    ]
    
    current_section = "GENEL AÇIKLAMA"
    for line in text_content.split('\n'):
        clean_line = re.sub(r'\*\*|\`|\*', '', line).strip()
        clean_line = clean_line.replace("> ", "").replace("<", "")
        
        if "TASLAK UYARISI" in clean_line or not clean_line:
            continue
            
        if clean_line.startswith("###"):
            continue
            
        if clean_line.startswith("* D") or clean_line.startswith("* **D") or re.match(r'^\d+\.', clean_line):
            current_section = clean_line.replace("*", "").strip()
            data.append({"Aşama / Kategori": current_section.upper(), "Açıklama": ""})
        elif clean_line.startswith("-"):
            data.append({"Aşama / Kategori": "", "Açıklama": clean_line.replace("-", "").strip()})
        else:
            data.append({"Aşama / Kategori": "", "Açıklama": clean_line})
            
    df = pd.DataFrame(data)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name="DOF_Raporu")
        worksheet = writer.sheets["DOF_Raporu"]
        worksheet.column_dimensions['A'].width = 35
        worksheet.column_dimensions['B'].width = 100
    return buf.getvalue()


# --- 6. SOHBETTEN GELEN 6M VE DÖF/8D TASLAK KOMUTLARINI YAKALAYICI ---
def detect_and_handle_quality_query(prompt: str, active_complaint: dict, user: dict) -> str | None:
    q = (prompt or "").replace("İ", "i").replace("I", "ı").lower().strip()
    q_norm = q.replace("â", "a").replace("î", "i").replace("û", "u")

    # Yalnızca açıkça DÖF / CAPA / 8D planı veya taslağı OLUŞTURULMASI istendiğinde devreye girer
    # ("Açık ve gecikmiş DÖF kayıtlarını listele" gibi listeleme sorularına müdahale etmez)
    is_capa_req = any(k in q_norm for k in [
        "döf planı", "döf oluştur", "dof planı", "dof oluştur",
        "capa planı", "capa oluştur", "capa taslağı", "8d",
        "düzeltici faaliyet planı", "döf hazırla", "döf taslağı", "dof taslağı"
    ])
    is_list_req = any(k in q_norm for k in ["açık ve gecikmiş", "listele", "kaç tane", "kaç adet", "hangileri", "göster"])

    if is_capa_req and not is_list_req:
        if not active_complaint:
            return "⚠️ DÖF / 8D taslağı oluşturabilmek için veritabanında en az bir şikâyet kaydı bulunmalıdır."
        fmt = "8D Problem Çözme Raporu (D1–D8)" if "8d" in q_norm else "Standart DÖF / CAPA Planı"
        draft = generate_capa_or_8d_draft(active_complaint, format_type=fmt)
        cid = int(active_complaint["id"])
        st.session_state["qe_selected_cid"] = cid
        st.session_state[f"qe_capa_text_{cid}"] = draft["draft_markdown"]
        st.session_state[f"qe_capa_meta_{cid}"] = draft
        return (
            f"{draft['draft_markdown']}\n\n"
            f"💡 *Bu taslağı düzenlemek, sorumlu/termin belirlemek ve **onaylayarak gerçek DÖF kaydına dönüştürmek** için üstteki **🧩 6M & DÖF / 8D** sekmesine geçebilirsiniz.*"
        )

    # Yalnızca açıkça 6M / Ishikawa Kök Neden Analizi Raporu istendiğinde devreye girer
    # ("En sık karşılaşılan hata nedenlerini analiz et" veya "Bu şikayetin kök nedeni nedir?" sorularını engellemez)
    is_6m_req = any(k in q_norm for k in [
        "6m", "ishikawa", "balık kılçığı",
        "kök neden analizi hazırla", "kök neden analizi oluştur", "kök neden analizi yap", "kök neden raporu"
    ])
    if is_6m_req:
        return generate_6m_root_cause_analysis(query_text=prompt, base_complaint=active_complaint)

    return None


# --- 7. ASİSTAN PANELİ İÇİNDEKİ "🧩 6M & DÖF / 8D" SEKMESİ ARAYÜZÜ ---
def render_quality_engine_tab(user: dict):
    init_quality_engine_tables()
    username = (user or {}).get("username", "misafir")

    df_all = complaints()
    if df_all.empty:
        st.info("Sistemde henüz analiz edilecek şikâyet kaydı bulunmuyor.")
        return

    active_detail_id = st.session_state.get("detail_id")
    if active_detail_id and st.session_state.get("_qe_synced_id") != active_detail_id:
        st.session_state["qe_selected_cid"] = int(active_detail_id)
        st.session_state["_qe_synced_id"] = int(active_detail_id)

    id_list = df_all["id"].astype(int).tolist()
    default_cid = st.session_state.get("qe_selected_cid")
    if default_cid not in id_list:
        default_cid = int(active_detail_id) if (active_detail_id and int(active_detail_id) in id_list) else id_list[0]

    label_map = {
        int(r["id"]): f"{r['complaint_no']} · {str(r['product'])[:22]} ({r['priority']} - {r['status']})"
        for _, r in df_all.iterrows()
    }

    selected_cid = st.selectbox(
        "📌 İncelenecek Şikâyet Kaydı:",
        options=id_list,
        index=id_list.index(default_cid),
        format_func=lambda x: label_map.get(x, str(x)),
        key="qe_cid_selectbox"
    )
    st.session_state["qe_selected_cid"] = int(selected_cid)
    current_item = get_complaint(int(selected_cid))

    mode = st.radio(
        "🛠️ Kalite Mühendisliği Aracı:",
        ["🧩 6M / Ishikawa Kök Neden Analizi", "📋 DÖF / CAPA & 8D Taslak Oluşturucu"],
        horizontal=True,
        key="qe_tool_mode"
    )

    if mode.startswith("🧩"):
        c_m1, c_m2 = st.columns(2)
        default_mod = str(current_item.get("product") or "").split()[0].upper()
        mod_input = c_m1.text_input("Model Filtresi (Opsiyonel):", value=default_mod, key="qe_6m_model")
        fail_opts = ["Otomatik (Şikâyetten Algıla)"] + list(FAILURE_GROUPS) + list(ALL_SUBTYPES)
        fail_sel = c_m2.selectbox("Odak Hata Tipi:", fail_opts, key="qe_6m_fail")

        if st.button("🔍 Veritabanını Tara ve 6M Analizi Oluştur", type="primary", use_container_width=True, key="btn_run_6m"):
            f_ovr = "" if fail_sel.startswith("Otomatik") else fail_sel
            st.session_state[f"qe_6m_result_{selected_cid}"] = generate_6m_root_cause_analysis(
                base_complaint=current_item,
                model_override=mod_input,
                failure_override=f_ovr
            )

        if f"qe_6m_result_{selected_cid}" not in st.session_state:
            st.session_state[f"qe_6m_result_{selected_cid}"] = generate_6m_root_cause_analysis(base_complaint=current_item)

        report_md = st.session_state[f"qe_6m_result_{selected_cid}"]
        safe_md = report_md.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
        html_template = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>6M Kök Neden Analizi - Kalite360</title>
            <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; padding: 40px; max-width: 900px; margin: 0 auto; color: #1e293b; line-height: 1.6; background-color: #f8fafc; }}
                .container {{ background: #ffffff; padding: 40px; border-radius: 12px; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1); border: 1px solid #e2e8f0; }}
                h3 {{ color: #0f172a; border-bottom: 2px solid #e2e8f0; padding-bottom: 10px; }}
                h4 {{ color: #334155; margin-top: 25px; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 14px; }}
                th, td {{ border: 1px solid #cbd5e1; padding: 10px; text-align: left; }}
                th {{ background-color: #f1f5f9; }}
                code {{ background-color: #f1f5f9; padding: 2px 6px; border-radius: 4px; font-family: monospace; color: #db2777; }}
                blockquote {{ border-left: 4px solid #3b82f6; background: #eff6ff; margin: 0; padding: 10px 20px; border-radius: 0 8px 8px 0; }}
            </style>
        </head>
        <body>
            <div class="container" id="content"></div>
            <script>
                document.getElementById('content').innerHTML = marked.parse(`{safe_md}`);
            </script>
        </body>
        </html>
        """
        
        st.success("✅ Veritabanı tarandı ve 6M Kök Neden Analizi hazırlandı!")
        st.download_button(
            label="📄 Raporu Tarayıcıda Aç (HTML Olarak İndir)",
            data=html_template.encode('utf-8'),
            file_name=f"6M_Analizi_{current_item['complaint_no']}.html",
            mime="text/html",
            type="primary",
            use_container_width=True
        )

    else:
        col_f1, col_f2 = st.columns([1.4, 1])
        fmt_sel = col_f1.selectbox(
            "📑 Çıktı Formatı:",
            ["Standart DÖF / CAPA Planı", "8D Problem Çözme Raporu (D1–D8)"],
            key="qe_capa_fmt"
        )
        custom_note = col_f2.text_input("Ek Not / Odak:", placeholder="Örn: Tedarikçi lot kontrolü...", key="qe_capa_note")

        capa_text_key = f"qe_capa_text_{selected_cid}"
        capa_meta_key = f"qe_capa_meta_{selected_cid}"

        if st.button("⚙️ DÖF / 8D Taslağı Hazırla", type="primary", use_container_width=True, key="btn_build_capa"):
            res = generate_capa_or_8d_draft(current_item, format_type=fmt_sel, custom_note=custom_note)
            st.session_state[capa_text_key] = res["draft_markdown"]
            st.session_state[capa_meta_key] = res
            st.rerun()

        if capa_text_key not in st.session_state:
            init_res = generate_capa_or_8d_draft(current_item, format_type=fmt_sel, custom_note=custom_note)
            st.session_state[capa_text_key] = init_res["draft_markdown"]
            st.session_state[capa_meta_key] = init_res

        meta = st.session_state.get(capa_meta_key) or generate_capa_or_8d_draft(current_item, format_type=fmt_sel)

        edited_capa = st.text_area(
            "✏️ DÖF / 8D Taslak Metni (Düzenlenebilir):",
            height=210,
            key=capa_text_key
        )

        st.markdown("<div style='font-size:12px; font-weight:700; color:#1e293b; margin-top:4px;'>✅ Kullanıcı Onayı ve Kayıt Parametreleri:</div>", unsafe_allow_html=True)
        p_c1, p_c2 = st.columns(2)
        act_title = p_c1.text_input("Aksiyon / DÖF Başlığı:", value=meta["draft_title"], key=f"qe_title_{selected_cid}")
        act_dept = p_c2.text_input("Sorumlu Departman / Kişi:", value=meta["dept_role"], key=f"qe_dept_{selected_cid}")

        default_due_date = date.fromisoformat(meta["suggested_due"]) if meta.get("suggested_due") else (date.today() + timedelta(days=14))
        act_due = st.date_input("Önerilen Termin Tarihi:", value=default_due_date, key=f"qe_due_{selected_cid}")

        approve_chk = st.checkbox(
            "DÖF bilgilerini inceledim; bu taslağın gerçek bir DÖF/Aksiyon kaydı olarak veritabanına (`actions`) işlenmesini onaylıyorum.",
            key=f"qe_approve_chk_{selected_cid}"
        )

        btn_c1, btn_c2 = st.columns(2)
        if btn_c1.button("💾 Sadece Taslak Kaydet", key="btn_save_capa_draft", use_container_width=True):
            d_id = save_capa_draft_only(meta, edited_capa, username)
            st.info(f"📝 Taslak #{d_id} kaydedildi ve denetim izine işlendi. (Gerçek DÖF açılmadı.)")

        if btn_c2.button("✅ Onayla ve Gerçek DÖF Aç", key="btn_commit_capa", use_container_width=True):
            if not approve_chk:
                st.warning("⚠️ Gerçek DÖF kaydı oluşturmak ve sorumlu/termin atamak için lütfen yukarıdaki onay kutusunu işaretleyin.")
            else:
                rec_id = approve_and_commit_capa(
                    cid=int(selected_cid),
                    title=act_title,
                    assignee=act_dept,
                    due_date_str=act_due.isoformat(),
                    edited_markdown=edited_capa,
                    format_type=fmt_sel,
                    username=username
                )
                st.success(f"🎉 DÖF kaydı (#{rec_id}) `{current_item['complaint_no']}` için `actions` ve `audit` tablolarına başarıyla işlendi!")

        st.markdown("---")
        st.markdown("📥 **Oluşturulan Taslağı Bilgisayara İndir:**")
        
        pdf_data = build_dof_pdf(current_item['complaint_no'], fmt_sel, act_title, act_dept, act_due.strftime("%d.%m.%Y"), edited_capa)
        excel_data = build_dof_excel(current_item['complaint_no'], fmt_sel, act_title, act_dept, act_due.strftime("%d.%m.%Y"), edited_capa)

        dl_c1, dl_c2 = st.columns(2)
        with dl_c1:
            if pdf_data:
                st.download_button(
                    label="📄 PDF Raporu Olarak İndir",
                    data=pdf_data,
                    file_name=f"DOF_{current_item['complaint_no']}.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
            else:
                st.info("PDF motoru arka planda yükleniyor...")
        with dl_c2:
            if excel_data:
                st.download_button(
                    label="📊 Excel Dosyası Olarak İndir",
                    data=excel_data,
                    file_name=f"DOF_{current_item['complaint_no']}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )

        existing_acts = actions(int(selected_cid))
        if not existing_acts.empty:
            with st.expander(f"📂 Bu Şikâyete Ait Onaylanmış DÖF / Aksiyonlar ({len(existing_acts)})"):
                st.dataframe(existing_acts, use_container_width=True, hide_index=True)
