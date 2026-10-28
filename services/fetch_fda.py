import requests
from urllib.parse import quote
from datetime import datetime
from db import db, create_complaint

def fetch_dynamic_fda(
    brand_name="",
    model_number="",
    product_code="",
    manufacturer="",
    event_type="Tümü",
    date_from=None,
    date_to=None,
    limit=5000
):
    query_parts = []
    
    if model_number:
        m_clean = model_number.strip()
        m_q = quote(f'"{m_clean}"')
        query_parts.append(f"(device.model_number.exact:{m_q}+OR+device.catalog_number.exact:{m_q})")
    if brand_name:
        b_q = quote(f'"{brand_name.strip()}"')
        query_parts.append(f"device.brand_name:{b_q}")
    if product_code:
        p_q = quote(f'"{product_code.strip()}"')
        query_parts.append(f"device.device_report_product_code.exact:{p_q}")
    if manufacturer:
        mfr_q = quote(f'"{manufacturer.strip()}"')
        query_parts.append(f"device.manufacturer_d_name:{mfr_q}")
        
    event_map = {
        "Ölüm (Death)": "Death",
        "Yaralanma (Injury)": "Injury",
        "Arıza (Malfunction)": "Malfunction"
    }
    if event_type in event_map:
        ev_q = quote(f'"{event_map[event_type]}"')
        query_parts.append(f"event_type:{ev_q}")
        
    if date_from and date_to:
        d1 = date_from.strftime("%Y%m%d")
        d2 = date_to.strftime("%Y%m%d")
        query_parts.append(f"date_received:[{d1}+TO+{d2}]")
        
    if not query_parts:
        return 0, 0
        
    search_query = "+AND+".join(query_parts)
    total_to_fetch = min(int(limit), 10000) 
    
    all_results = []
    skip = 0
    
    while len(all_results) < total_to_fetch:
        batch_size = min(100, total_to_fetch - len(all_results))
        url = f"https://api.fda.gov/device/event.json?search={search_query}&sort=date_received:desc&limit={batch_size}&skip={skip}"
        try:
            res = requests.get(url, timeout=25)
            if res.status_code != 200:
                break
            data = res.json()
            batch = data.get("results", [])
            if not batch:
                break
            all_results.extend(batch)
            if len(batch) < batch_size:
                break
            skip += batch_size
        except Exception:
            break
            
    if not all_results:
        return 0, 0
        
    added = 0
    skipped = 0
    
    with db() as conn:
        existing_orders = {
            str(r[0]).strip()
            for r in conn.execute("SELECT order_no FROM complaints WHERE order_no IS NOT NULL").fetchall()
        }
        
    target_model = model_number.strip().upper() if model_number else ""
        
    for item in all_results:
        mdr_no = str(item.get("mdr_report_key") or item.get("report_number") or "").strip()
        if not mdr_no:
            continue
        if mdr_no in existing_orders:
            skipped += 1
            continue
            
        devices = item.get("device", [{}])
        dev = devices[0] if devices else {}
        
        api_model = str(dev.get("model_number") or "").strip().upper()
        api_catalog = str(dev.get("catalog_number") or "").strip().upper()
        
        if target_model:
            if target_model != api_model and target_model != api_catalog:
                skipped += 1
                continue
                
        b_name = str(dev.get("brand_name") or brand_name or "Tıbbi Cihaz").strip()
        mfr_name = str(dev.get("manufacturer_d_name") or manufacturer or "Üretici Belirtilmemiş").strip()
        
        actual_model = target_model or api_model or api_catalog
        product_display = f"{b_name} ({actual_model}) [{mfr_name}]" if actual_model else f"{b_name} [{mfr_name}]"
            
        mdr_texts = item.get("mdr_text", [])
        problem_desc = ""
        mfr_narrative = ""
        
        for t in mdr_texts:
            t_type = str(t.get("text_type_code", "")).lower()
            txt = str(t.get("text", "")).strip()
            if not txt:
                continue
            if "description of event" in t_type or "problem" in t_type:
                problem_desc += txt + "\n"
            elif "manufacturer narrative" in t_type or "evaluation" in t_type:
                mfr_narrative += txt + "\n"
            else:
                if not problem_desc:
                    problem_desc = txt
                else:
                    mfr_narrative += txt + "\n"
                    
        problem_desc = problem_desc.strip()
        mfr_narrative = mfr_narrative.strip()
        
        # 🧹 ÇÖP FİLTRESİ: Eğer müşteri şikayeti yoksa VE üretici raporu yoksa bu kaydı sisteme alma!
        if not problem_desc and not mfr_narrative:
            skipped += 1
            continue
            
        problem_desc = problem_desc or "FDA MAUDE kaydında olay metni belirtilmemiş."
        
        ev_type = str(item.get("event_type") or "Malfunction").strip()
        priority = "Kritik" if ev_type.lower() in ["death", "injury", "serious injury"] else "Orta"
        urgent = (priority == "Kritik")
        
        date_rec = str(item.get("date_received") or "")
        if len(date_rec) == 8:
            created_iso = f"{date_rec[:4]}-{date_rec[4:6]}-{date_rec[6:8]}T10:00:00"
        else:
            created_iso = datetime.now().isoformat(timespec="seconds")
            
        findings_meta = f"Üretici: {mfr_name} | Olay Türü: {ev_type} | Model: {actual_model or '-'}"
        status = "Kapalı" if mfr_narrative else "İncelemede"
        
        cid = create_complaint(
            customer="FDA MAUDE Bildirimi",
            contact="Klinik / Hastane Raporu",
            phone="-",
            email="",
            product=product_display,
            order=mdr_no,
            category="Ürün Kalitesi",
            priority=priority,
            description=problem_desc,
            requested="Teknik servis incelemesi ve kök neden analizi",
            owner="kalite",
            user="admin",
            urgent=urgent
        )
        
        with db() as conn:
            conn.execute(
                """UPDATE complaints 
                   SET created_at=?, updated_at=?, status=?, findings=?, action_plan=? 
                   WHERE id=?""",
                (created_iso, created_iso, status, findings_meta, mfr_narrative, cid)
            )
            
        existing_orders.add(mdr_no)
        added += 1
        
    return added, skipped