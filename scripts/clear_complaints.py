import sqlite3
from db import DB

def clear_all():
    with sqlite3.connect(DB) as conn:
        cursor = conn.cursor()
        
        # 1. Veritabanında gerçekten var olan tabloları çek
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = [row[0] for row in cursor.fetchall()]
        
        # 2. Silinmesi hedeflenen tablolar (Kullanıcı hesapları 'users' HARİÇ tutuldu)
        target_tables = ['complaints', 'actions', 'communications', 'audit', 'email_ingest']
        
        for table in target_tables:
            if table in existing_tables:
                cursor.execute(f"DELETE FROM {table}")
                print(f"🗑️ '{table}' tablosu temizlendi.")
        
        # 3. ID sayacını (Auto-increment) sıfırla
        if 'sqlite_sequence' in existing_tables:
            for table in target_tables:
                cursor.execute(f"DELETE FROM sqlite_sequence WHERE name='{table}'")
                
        conn.commit()
    print("✅ Tüm şikâyet verileri sıfırlandı! Kullanıcı giriş hesapları korundu.")

if __name__ == "__main__":
    clear_all()