import re

def extract_complaint_text(raw):
    text=(raw or '').strip(); low=text.lower()
    def cap(patterns):
        for pattern in patterns:
            m=re.search(pattern,text,re.I|re.M)
            if m:return m.group(1).strip(' \t\r\n:;,-')
        return ''
    email=cap([r'([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})'])
    phone=cap([r'((?:\+?90|0)?\s?5\d{2}[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2})'])
    customer=cap([r'(?:müşteri|firma|şirket)\s*[:=-]\s*([^\n,;]+)',r'(?:merhaba|sayın)\s+([^,\n]+)'])
    product=cap([r'(?:ürün|parça|product)\s*[:=-]\s*([^\n,;]+)',r'\b([A-ZÇĞİÖŞÜa-zçğıöşü0-9-]{2,}\s+(?:parçası|parça|ürünü|ürün))\b'])
    order=cap([r'(?:sipariş(?:\s+no(?:su)?)?|iş\s*emri|order\s*(?:no)?)\s*[:#=-]?\s*([A-Z0-9][A-Z0-9-]{3,})'])
    contact=cap([r'(?:iletişim kişisi|ilgili kişi|yetkili)\s*[:=-]\s*([^\n,;]+)'])
    category='Diğer'
    if any(x in low for x in ['tolerans','ölçü','çap','boyut','ölçüm']):category='Ölçü / Tolerans'
    elif any(x in low for x in ['çizik','çatlak','yüzey','çapak','pas']):category='Yüzey Hatası'
    elif any(x in low for x in ['geç teslim','teslimat','gecikti','eksik teslim']):category='Teslimat'
    elif any(x in low for x in ['ambalaj','paketleme','kutu']):category='Paketleme'
    elif any(x in low for x in ['hizmet','servis','teknisyen']):category='Hizmet'
    elif any(x in low for x in ['kalite','hatalı','arızalı','bozuk','kusur']):category='Ürün Kalitesi'
    priority='Yüksek' if any(x in low for x in ['acil','üretim durdu','hat durdu','kritik']) else ('Orta' if any(x in low for x in ['şikayet','şikâyet','hata','sorun','kusur']) else 'Düşük')
    requested=cap([r'(?:talep|beklenti|rica|çözüm)\s*[:=-]\s*([^\n]+)'])
    urgent=any(x in low for x in ['fabrikaya geri','geri gönder','iade alın','acil arayın','acil dönüş','yerinde servis'])
    return {'customer':customer,'contact':contact,'phone':phone,'email':email,'product':product,'order':order,'category':category,'priority':priority,'description':text,'requested':requested,'urgent':urgent}
