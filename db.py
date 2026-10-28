import sqlite3, hashlib, os, re
from pathlib import Path
from datetime import datetime
import pandas as pd

DB=Path(__file__).parent/"data"/"kalite360.db"; DB.parent.mkdir(exist_ok=True)
CATEGORIES=["Ürün Kalitesi","Ölçü / Tolerans","Yüzey Hatası","Teslimat","Paketleme","Hizmet","Diğer"]
STATUSES=["Yeni","İncelemede","Aksiyon Bekliyor","Doğrulama","Kapalı"]
VALIDITIES=["Değerlendirilmedi","Geçerli şikâyet","Yersiz / yanlış şikâyet"]
ACTION_TYPES=["İnceleme gerekli","Servise gönder","Fabrikaya geri çağır","Yerinde servis","Parça değişimi","Onarım","Kullanım talimatı / bilgilendirme","Yersiz / yanlış şikâyet","Diğer"]

def db():
 c=sqlite3.connect(DB,timeout=10); c.row_factory=sqlite3.Row; c.execute("PRAGMA foreign_keys=ON"); return c
def pw_hash(p,s): return hashlib.pbkdf2_hmac("sha256",p.encode(),s.encode(),180000).hex()

def calculate_nlp_priority(description="", findings="", requested="", urgent=False):
 """Şikâyet metnini NLP ile analiz ederek Compound Skor (-1.0..+1.0), Dinamik Skor (0..100), Öncelik Sınıfı ve Gerekçe üretir."""
 raw = f"{description or ''} {findings or ''} {requested or ''}".strip()
 t = raw.lower()
 if not t: return 0.0, 25.0, "Düşük", "Metin belirtilmemiş"

 score = -0.05
 risks, tones = [], []

 # 1. Ameliyathane ve Hasta Riski Sözlüğü (NLP Ağırlıkları)
 risk_rules = [
  (["death","deceased","fatal","ölüm","ex oldu","hayati tehlike"], -0.60, "Hayati tehlike/ölüm riski"),
  (["dural tear","csf","nerve","dura","sinir hasarı","dural yırtık"], -0.45, "Dural yırtık/sinir hasarı riski"),
  (["serious injury","injury","burn","bleeding","patient harm","yaralanma","yanık","kanama","doku hasarı"], -0.38, "Hasta yaralanması/klinik zarar"),
  (["intraoperative","during surgery","during procedure","ameliyat esnasında","vaka sırasında","operasyon sırasında","cerrahi"], -0.22, "Ameliyat esnasında vaka"),
  (["overheating","overheat","extremely hot","thermal","aşırı ısınma","aşırı ısın","çok ısındı","sıcaklık"], -0.24, "Aşırı ısınma/termal risk"),
  (["broke","broken","fracture","detached","fragment","separated","kırıldı","koptu","parça fırladı","çatlak"], -0.22, "Mekanik kırılma/parça kopması"),
  (["stuck","locked","seized","collet","sıkıştı","kilitlendi","pens çıkmıyor"], -0.18, "Mekanik kilitlenme/sıkışma"),
  (["shock","spark","leakage","hipot","smoke","elektrik kaçağı","kıvılcım","duman"], -0.28, "Elektriksel güvenlik riski"),
  (["bearing","worn","noise","vibration","rulman","aşınma","anormal ses","titreşim"], -0.14, "Rulman aşınması/mekanik kusur"),
  (["stopped working","did not work","failed to","çalışmadı","durdu","arıza"], -0.12, "Cihaz fonksiyon kaybı")
 ]
 for words, w_val, label in risk_rules:
  if any(w in t for w in words):
   score += w_val; risks.append(label)

 # Hasta zararı olmadığını belirten nötrleyici/yumuşatıcı klinik ifadeler
 no_harm = any(w in t for w in ["no patient","no injury","no harm","no impact","no health consequences","hiçbir etki bildirilmedi","hasta veya personel etkisi","hasta zararı yok","klinik belirti yok"])
 if no_harm:
  score += 0.16
  risks.append("hasta/personel zararı yok")

 # 2. Cerrahın / Bildirimin Aciliyet Tonu Sözlüğü
 tone_rules = [
  (["urgent","immediately","critical","emergency","unacceptable","acil","derhal","hemen","kritik","hat durdu"], -0.22, "Yüksek aciliyet tonu"),
  (["delay","prolonged","aborted","cancelled","gecikme","ameliyat uzadı","vaka iptal"], -0.18, "Operasyon gecikmesi"),
  (["90 days","within 90","90 günden kısa","erken arıza","yeni cihaz","tekrar","again","repeated"], -0.11, "Erken/tekrarlayan arıza bildirimi"),
  (["return","replace","repair","investigation","onarım talebi","iade","değişim"], -0.06, "Servis/onarım talebi")
 ]
 for words, w_val, label in tone_rules:
  if any(w in t for w in words):
   score += w_val; tones.append(label)

 if urgent:
  score -= 0.15; tones.append("Acil dönüş talebi")

 # Metin içeriğine göre mikro-hassasiyet (Her farklı şikâyet metninin skorunun kendine özgü olması için)
 word_cnt = len(re.findall(r'\w+', t))
 exclam_cnt = raw.count("!")
 micro_shift = -min(0.06, (word_cnt % 13) * 0.004 + exclam_cnt * 0.015)
 score = max(-0.98, min(0.85, round(score + micro_shift, 2)))

 # Compound Skordan (-1.0 .. +1.0) Dinamik Öncelik Skoruna (0 .. 100) Dönüşüm
 rank = round(max(5.0, min(99.5, ((1.0 - score) / 2.0) * 100.0)), 1)

 # Compound Skora Göre Önem Derecesi Sınıflandırması
 if score <= -0.60: prio_label = "Kritik"
 elif score <= -0.32: prio_label = "Yüksek"
 elif score <= -0.10: prio_label = "Orta"
 else: prio_label = "Düşük"

 summary_parts = (risks[:2] + tones[:1]) if (risks or tones) else ["Standart teknik bildirim"]
 reason_str = ", ".join(summary_parts)
 return score, rank, prio_label, reason_str

def init_db():
 with db() as c:
  c.executescript("""CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,full_name TEXT,role TEXT,salt TEXT,hash TEXT,active INTEGER DEFAULT 1);
  CREATE TABLE IF NOT EXISTS complaints(id INTEGER PRIMARY KEY AUTOINCREMENT,complaint_no TEXT UNIQUE,customer TEXT,contact TEXT,phone TEXT,email TEXT,product TEXT,order_no TEXT,category TEXT,priority TEXT,description TEXT,requested TEXT,owner TEXT,status TEXT,findings TEXT,root_cause TEXT,action_type TEXT,action_plan TEXT,validity TEXT,urgent_return INTEGER,sentiment_score REAL,priority_rank REAL,priority_reason TEXT,created_by TEXT,created_at TEXT,updated_at TEXT,closed_at TEXT);
  CREATE TABLE IF NOT EXISTS actions(id INTEGER PRIMARY KEY,complaint_id INTEGER,title TEXT,assignee TEXT,due_date TEXT,status TEXT DEFAULT 'Planlandı',created_by TEXT,created_at TEXT);
  CREATE TABLE IF NOT EXISTS contacts(id INTEGER PRIMARY KEY,complaint_id INTEGER,channel TEXT,outcome TEXT,notes TEXT,created_by TEXT,created_at TEXT);
  CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,complaint_id INTEGER,username TEXT,event TEXT,details TEXT,created_at TEXT);""")
  cols=[r[1] for r in c.execute("PRAGMA table_info(complaints)").fetchall()]
  if "sentiment_score" not in cols: c.execute("ALTER TABLE complaints ADD COLUMN sentiment_score REAL")
  if "priority_rank" not in cols: c.execute("ALTER TABLE complaints ADD COLUMN priority_rank REAL")
  if "priority_reason" not in cols: c.execute("ALTER TABLE complaints ADD COLUMN priority_reason TEXT")
  for u,n,r,p in [("musterihizmetleri","Müşteri Hizmetleri","Müşteri Hizmetleri","MH2026!"),("kalite","Kalite Personeli","Kalite","Kalite2026!"),("admin","Sistem Yöneticisi","Yönetici","Admin2026!")]:
   if not c.execute("SELECT id FROM users WHERE username=?",(u,)).fetchone():
    s=os.urandom(16).hex(); c.execute("INSERT INTO users(username,full_name,role,salt,hash) VALUES(?,?,?,?,?)",(u,n,r,s,pw_hash(p,s)))

def authenticate(u,p):
 with db() as c: r=c.execute("SELECT * FROM users WHERE username=? AND active=1",(u.strip(),)).fetchone()
 return {"username":r["username"],"full_name":r["full_name"],"role":r["role"]} if r and pw_hash(p,r["salt"])==r["hash"] else None
def log(c,cid,u,e,d=""): c.execute("INSERT INTO audit(complaint_id,username,event,details,created_at) VALUES(?,?,?,?,?)",(cid,u,e,d,datetime.now().isoformat(timespec="seconds")))

def create_complaint(customer,contact,phone,email,product,order,category,priority,description,requested,owner,user,urgent):
 now=datetime.now().isoformat(timespec="seconds")
 s_score, p_rank, auto_prio, p_reason = calculate_nlp_priority(description, "", requested, urgent)
 final_prio = auto_prio if auto_prio in ["Kritik","Yüksek","Orta","Düşük"] else (priority or "Orta")
 with db() as c:
  cur=c.execute("INSERT INTO complaints(customer,contact,phone,email,product,order_no,category,priority,description,requested,owner,status,findings,root_cause,action_type,action_plan,validity,urgent_return,sentiment_score,priority_rank,priority_reason,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(customer,contact,phone,email,product,order,category,final_prio,description,requested,owner,"Yeni","","","İnceleme gerekli","","Değerlendirilmedi",int(urgent),s_score,p_rank,p_reason,user,now,now))
  cid=cur.lastrowid; c.execute("UPDATE complaints SET complaint_no=? WHERE id=?",(f"SK-{datetime.now():%Y}-{cid:06d}",cid)); log(c,cid,user,"Şikâyet oluşturuldu",f"NLP={s_score:+.2f}; Skor={p_rank}; Öncelik={final_prio}")
  if urgent: c.execute("INSERT INTO contacts(complaint_id,channel,outcome,notes,created_by,created_at) VALUES(?,?,?,?,?,?)",(cid,"Sistem","Bekliyor","Acil geri dönüş görevi açıldı.",user,now))
  return cid

def complaints():
 with db() as c: rows=c.execute("SELECT * FROM complaints ORDER BY id DESC").fetchall()
 return pd.DataFrame([dict(r) for r in rows])
def get_complaint(cid):
 with db() as c: r=c.execute("SELECT * FROM complaints WHERE id=?",(cid,)).fetchone()
 return dict(r) if r else None
def save_investigation(cid,findings,root,atype,plan,validity,urgent,status,user):
 now=datetime.now().isoformat(timespec="seconds")
 with db() as c:
  c.execute("UPDATE complaints SET findings=?,root_cause=?,action_type=?,action_plan=?,validity=?,urgent_return=?,status=?,updated_at=?,closed_at=? WHERE id=?",(findings,root,atype,plan,validity,int(urgent),status,now,now if status=="Kapalı" else None,cid))
  log(c,cid,user,"İnceleme güncellendi",f"{status}; {atype}; {validity}; acil={urgent}")
def add_action(cid,title,assignee,due,user):
 with db() as c: c.execute("INSERT INTO actions(complaint_id,title,assignee,due_date,created_by,created_at) VALUES(?,?,?,?,?,?)",(cid,title,assignee,due,user,datetime.now().isoformat(timespec="seconds"))); log(c,cid,user,"Aksiyon oluşturuldu",title)
def actions(cid):
 with db() as c: rows=c.execute("SELECT title,assignee,due_date,status,created_at FROM actions WHERE complaint_id=? ORDER BY id DESC",(cid,)).fetchall()
 return pd.DataFrame([dict(r) for r in rows])
def add_contact(cid,ch,outcome,notes,user):
 with db() as c: c.execute("INSERT INTO contacts(complaint_id,channel,outcome,notes,created_by,created_at) VALUES(?,?,?,?,?,?)",(cid,ch,outcome,notes,user,datetime.now().isoformat(timespec="seconds"))); log(c,cid,user,"Müşteri iletişimi",f"{ch}; {outcome}; {notes}")
def communications(cid):
 with db() as c: rows=c.execute("SELECT channel,outcome,notes,created_by,created_at FROM contacts WHERE complaint_id=? ORDER BY id DESC",(cid,)).fetchall()
 return pd.DataFrame([dict(r) for r in rows])
def audit_history(cid):
 with db() as c: rows=c.execute("SELECT username,event,details,created_at FROM audit WHERE complaint_id=? ORDER BY id DESC",(cid,)).fetchall()
 return pd.DataFrame([dict(r) for r in rows])
def metrics():
 with db() as c:
  return {"total":c.execute("SELECT COUNT(*) FROM complaints").fetchone()[0],"open":c.execute("SELECT COUNT(*) FROM complaints WHERE status!='Kapalı'").fetchone()[0],"investigating":c.execute("SELECT COUNT(*) FROM complaints WHERE status='İncelemede'").fetchone()[0],"urgent":c.execute("SELECT COUNT(*) FROM complaints WHERE urgent_return=1 AND status!='Kapalı'").fetchone()[0],"invalid":c.execute("SELECT COUNT(*) FROM complaints WHERE validity='Yersiz / yanlış şikâyet'").fetchone()[0]}