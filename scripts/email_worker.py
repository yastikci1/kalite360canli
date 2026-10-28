"""Kalite360 IMAP arka plan işçisi. Ayrı terminalde: python email_worker.py"""
import os
import time
import logging
from services.email_ingest import poll_inbox

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
INTERVAL = max(60, int(os.getenv("POLL_INTERVAL_SECONDS", "180")))

if __name__ == "__main__":
    logging.info("Kalite360 e-posta işçisi başladı; kontrol aralığı %s saniye.", INTERVAL)
    while True:
        try:
            result = poll_inbox("email-worker")
            if result.get("ok"):
                logging.info(result.get("message", "Kontrol tamamlandı."))
            else:
                logging.warning(result.get("message", "E-posta kontrolü başarısız."))
        except Exception:
            logging.exception("E-posta kontrolünde beklenmeyen hata")
        time.sleep(INTERVAL)
