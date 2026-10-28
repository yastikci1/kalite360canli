"""IMAP alım servisi: yalnızca belirlenen konu etiketi ve isteğe bağlı gönderici filtresi."""
import os, imaplib, email, re, sqlite3
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from pathlib import Path
from datetime import datetime
from db import DB, create_complaint
from app_helpers import extract_complaint_text
from services.mail_service import load_local_env

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / '.env'

def settings():
    load_local_env()
    return {
        "host": os.getenv("IMAP_HOST", ""),
        "port": os.getenv("IMAP_PORT", "993"),
        "username": os.getenv("IMAP_USER", ""),
        "password": os.getenv("IMAP_PASSWORD", ""),
        "folder": os.getenv("IMAP_FOLDER", "INBOX"),
        "subject_tag": os.getenv("IMAP_SUBJECT_TAG", "[SIKAYET]"),
        "sender_allowlist": os.getenv("IMAP_SENDER_ALLOWLIST", "")
    }

def save_settings(values):
    existing = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                existing[k.strip()] = v.strip()
    for k, v in values.items():
        s = str(v).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '')
        existing[k] = '"' + s + '"'
    ENV_PATH.write_text('# Kalite360 gizli ayarları; paylaşmayın.\n' + '\n'.join(f'{k}={v}' for k, v in existing.items()) + '\n', encoding='utf-8')
    for k, v in values.items():
        os.environ[k] = str(v)

def _decoded(value):
    if not value:
        return ''
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)

def _body(msg):
    parts = []
    if msg.is_multipart():
        for p in msg.walk():
            if p.get_content_type() == 'text/plain' and not p.get_filename():
                try:
                    parts.append(p.get_content())
                except Exception:
                    try:
                        parts.append(p.get_payload(decode=True).decode(p.get_content_charset() or 'utf-8', 'replace'))
                    except Exception:
                        pass
    elif msg.get_content_type() == 'text/plain':
        try:
            parts.append(msg.get_content())
        except Exception:
            pass
    return '\n'.join(parts).strip()[:20000]

def _clean_text(text: str) -> str:
    """Türkçe karakterleri ve parantezleri temizleyerek esnek eşleştirme sağlar."""
    if not text:
        return ""
    t = text.lower()
    tr_map = {
        'ş': 's', 'ı': 'i', 'i̇': 'i', 'ğ': 'g', 'ü': 'u', 'ö': 'o', 'ç': 'c',
        '[': '', ']': '', '(': '', ')': '', '-': '', '_': '', ' ': ''
    }
    for k, v in tr_map.items():
        t = t.replace(k, v)
    return t

def poll_inbox(username='admin'):
    s = settings()
    if not all([s['host'], s['username'], s['password'], s['subject_tag']]):
        return {"ok": False, "message": "IMAP sunucusu, kullanıcı, uygulama parolası ve konu etiketi girilmelidir.", "imported": 0, "ignored": 0}
    
    imported = 0
    already_imported = 0
    ignored = 0
    inspected_subjects = []
    
    try:
        with imaplib.IMAP4_SSL(s['host'], int(s['port']), timeout=25) as imap:
            imap.login(s['username'], s['password'])
            typ, _ = imap.select(s['folder'], readonly=False)
            if typ != 'OK':
                return {"ok": False, "message": f"'{s['folder']}' klasörü açılamadı.", "imported": 0, "ignored": 0}
            
            # Tüm maillerin UID listesini al
            typ, data = imap.uid('search', None, 'ALL')
            if typ != 'OK' or not data or not data[0]:
                return {"ok": True, "message": f"Gelen kutusunda ({s['folder']}) hiç e-posta bulunamadı.", "imported": 0, "ignored": 0}
            
            uids = data[0].split()
            recent_uids = uids[-30:]  # Son 30 e-postayı tara
            allow = [x.strip().lower() for x in s['sender_allowlist'].split(',') if x.strip()]
            tag_clean = _clean_text(s['subject_tag'])
            
            # Yeniden eskiye doğru tara
            for uid in reversed(recent_uids):
                # Yalnızca başlıkları hafif çek (hızlı)
                typ, h_parts = imap.uid('fetch', uid, '(RFC822.HEADER)')
                if typ != 'OK' or not h_parts or not h_parts[0]:
                    continue
                
                h_msg = email.message_from_bytes(h_parts[0][1])
                subject = _decoded(h_msg.get('Subject', ''))
                sender = email.utils.parseaddr(h_msg.get('From', ''))[1].lower()
                
                if len(inspected_subjects) < 4 and subject:
                    inspected_subjects.append(f"\"{subject}\"")
                
                sub_clean = _clean_text(subject)
                
                # Esnek eşleşme kontrolü
                matches_tag = (tag_clean in sub_clean) or (s['subject_tag'].lower() in subject.lower())
                matches_sender = (not allow) or any(a in sender for a in allow)
                
                if not matches_tag or not matches_sender:
                    ignored += 1
                    continue
                
                # Veritabanında daha önce alınıp alınmadığını kontrol et
                with sqlite3.connect(DB) as c:
                    c.execute("""CREATE TABLE IF NOT EXISTS email_ingest(
                        mailbox TEXT, folder TEXT, uid TEXT, complaint_id INTEGER,
                        imported_at TEXT, viewed_at TEXT,
                        PRIMARY KEY(mailbox,folder,uid))""")
                    if c.execute('SELECT 1 FROM email_ingest WHERE mailbox=? AND folder=? AND uid=?', (s['username'], s['folder'], uid.decode())).fetchone():
                        already_imported += 1
                        continue
                
                # Eşleşti ve yeni -> Gövdeyi indir
                typ, parts = imap.uid('fetch', uid, '(RFC822)')
                if typ != 'OK' or not parts or not parts[0]:
                    continue
                
                msg = email.message_from_bytes(parts[0][1])
                body = _body(msg) or '(E-posta gövdesi boş veya metin biçiminde değil.)'
                full = f"Konu: {subject}\nGönderen: {sender}\n\n{body}"
                parsed = extract_complaint_text(full)
                
                cid = create_complaint(
                    parsed['customer'] or sender or 'E-posta ile gelen müşteri',
                    parsed['contact'],
                    parsed['phone'],
                    sender,
                    parsed['product'],
                    parsed['order'],
                    parsed['category'],
                    parsed['priority'],
                    full,
                    parsed['requested'],
                    'Müşteri Hizmetleri',
                    username,
                    parsed['urgent']
                )
                
                try:
                    received = parsedate_to_datetime(msg.get('Date'))
                    if received and received.tzinfo:
                        received = received.astimezone().replace(tzinfo=None)
                    if received:
                        with sqlite3.connect(DB) as c:
                            c.execute('UPDATE complaints SET created_at=?,updated_at=? WHERE id=?', (received.isoformat(timespec='seconds'), received.isoformat(timespec='seconds'), cid))
                except Exception:
                    pass
                
                with sqlite3.connect(DB) as c:
                    c.execute('INSERT OR IGNORE INTO email_ingest(mailbox, folder, uid, complaint_id, imported_at) VALUES(?,?,?,?,?)', (s['username'], s['folder'], uid.decode(), cid, datetime.now().isoformat(timespec="seconds")))
                
                imap.uid('store', uid, '+FLAGS', '(\\Seen)')
                imported += 1
            
            imap.logout()
        
        if imported > 0:
            return {"ok": True, "message": f"🎉 Başarılı! {imported} adet yeni şikâyet e-postası sisteme aktarıldı.", "imported": imported, "ignored": ignored}
        elif already_imported > 0:
            return {"ok": True, "message": f"ℹ️ Filtreye uyan {already_imported} adet e-posta bulundu, ancak daha önce zaten sisteme kaydedilmiş.", "imported": 0, "ignored": ignored}
        else:
            sub_info = " | ".join(inspected_subjects) if inspected_subjects else "Başlık okunamadı"
            return {"ok": False, "message": f"⚠️ Son maillerde '{s['subject_tag']}' bulunamadı. Gelen son başlıklar: {sub_info}", "imported": 0, "ignored": ignored}
            
    except Exception as exc:
        return {"ok": False, "message": f"IMAP bağlantı hatası: {type(exc).__name__}: {exc}", "imported": imported, "ignored": ignored}