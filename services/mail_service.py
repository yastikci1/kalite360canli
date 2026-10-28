"""SMTP e-posta servisi. Ayarlar proje kökündeki .env dosyasında saklanır."""
import os
import smtplib
import ssl
from pathlib import Path
from email.message import EmailMessage

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
PROVIDERS = {
    "Google / Gmail": {"host": "smtp.gmail.com", "port": "587"},
    "Microsoft 365 / Outlook": {"host": "smtp.office365.com", "port": "587"},
    "Özel SMTP sunucusu": {"host": "", "port": "587"},
}


def load_local_env():
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        # setdefault yerine doğrudan atama yapıyoruz:
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def save_mail_settings(provider: str, host: str, port: str, username: str, password: str, sender: str, notify_to: str):
    values = {
        "MAIL_PROVIDER": provider,
        "SMTP_HOST": host.strip(),
        "SMTP_PORT": str(port).strip(),
        "SMTP_USER": username.strip(),
        "SMTP_PASSWORD": password,
        "MAIL_FROM": sender.strip(),
        "QUALITY_NOTIFICATION_EMAIL": notify_to.strip(),
    }
    def quote(v):
        return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "") + '"'
    ENV_PATH.write_text("# Kalite360 e-posta ayarları; bu dosyayı paylaşmayın veya Git'e yüklemeyin.\n" + "\n".join(f"{k}={quote(v)}" for k, v in values.items()) + "\n", encoding="utf-8")
    for key, value in values.items():
        os.environ[key] = value


def mail_settings():
    load_local_env()
    return {
        "provider": os.getenv("MAIL_PROVIDER", "Google / Gmail"),
        "host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "port": os.getenv("SMTP_PORT", "587"),
        "username": os.getenv("SMTP_USER", ""),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "sender": os.getenv("MAIL_FROM", ""),
        "notify_to": os.getenv("QUALITY_NOTIFICATION_EMAIL", ""),
    }


def mail_status():
    s = mail_settings()
    required = ["host", "port", "username", "password", "sender"]
    missing = [key for key in required if not str(s.get(key, "")).strip()]
    if missing:
        return False, "E-posta ayarları tamamlanmamış. Yönetim > E-posta ayarları bölümünden şirketinizin sağlayıcısını seçin."
    return True, f"SMTP ayarlı: {s['provider']} · {s['host']}:{s['port']}"


def send_complaint_email(to_addr: str, subject: str, body: str) -> dict:
    ok, status = mail_status()
    if not ok:
        return {"ok": False, "error": status}
    s = mail_settings()
    msg = EmailMessage()
    msg["From"] = s["sender"]
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        context = ssl.create_default_context()
        port = int(s["port"])
        if port == 465:
            with smtplib.SMTP_SSL(s["host"], port, context=context, timeout=25) as server:
                server.login(s["username"], s["password"])
                server.send_message(msg)
        else:
            with smtplib.SMTP(s["host"], port, timeout=25) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(s["username"], s["password"])
                server.send_message(msg)
        return {"ok": True, "error": ""}
    except Exception as exc:
        return {"ok": False, "error": f"SMTP gönderim hatası: {type(exc).__name__}: {exc}"}

load_local_env()
