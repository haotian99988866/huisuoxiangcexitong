import psycopg2
import os
from dotenv import load_dotenv
load_dotenv()

def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASS")
    )

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS venues (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            address TEXT,
            phone TEXT,
            business_hours TEXT,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("SELECT COUNT(*) FROM venues")
    if cur.fetchone()[0] == 0:
        cur.execute("""
            INSERT INTO venues (name, address, phone, business_hours, description)
            VALUES (%s, %s, %s, %s, %s)
        """, ("Banlung精品会所", "Ratanakiri Banlung · 市中心路88号", "+855 88 123 4567", "10:00 — 24:00", "专注经络调理 / 精油SPA，静谧包间环境"))
    conn.commit()
    cur.close()
    conn.close()
    print("✅ 数据库就绪")

if __name__ == "__main__":
    init_db()