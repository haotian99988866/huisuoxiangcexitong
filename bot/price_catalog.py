"""Shared runtime price snapshot for public venue displays."""

import json
from decimal import Decimal
from pathlib import Path

from db import get_conn

_PATH = Path(__file__).resolve().parent.parent / "shared" / "venue-prices.json"


def initial_catalog():
    with _PATH.open(encoding="utf-8") as source:
        catalog = json.load(source)
    catalog["deliveryPrices"] = {"7": 170, "8": 180, "9": 160}
    return catalog


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS venue_price_catalog (
                id TINYINT PRIMARY KEY,
                revision BIGINT NOT NULL,
                catalog_json LONGTEXT NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS venue_price_audit (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                revision BIGINT NOT NULL,
                actor_id BIGINT NOT NULL,
                venue_id INT NOT NULL,
                service_key VARCHAR(64) NOT NULL,
                old_price DECIMAL(12,2) NULL,
                new_price DECIMAL(12,2) NULL,
                old_commission DECIMAL(12,2) NOT NULL,
                new_commission DECIMAL(12,2) NOT NULL,
                changed_at DATETIME NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS venue_price_channel_review (
                revision BIGINT NOT NULL,
                venue_id INT NOT NULL,
                status VARCHAR(16) NOT NULL,
                reason VARCHAR(255) NOT NULL,
                created_at DATETIME NOT NULL,
                PRIMARY KEY(revision,venue_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute(
                "INSERT IGNORE INTO venue_price_catalog(id,revision,catalog_json) VALUES(1,1,%s)",
                (json.dumps(initial_catalog(), ensure_ascii=False),),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def read_catalog(cur, lock=False):
    cur.execute("SELECT revision,catalog_json FROM venue_price_catalog WHERE id=1" +
                (" FOR UPDATE" if lock else ""))
    row = cur.fetchone()
    if not row:
        raise RuntimeError("统一价目尚未初始化")
    catalog = json.loads(row["catalog_json"])
    if not isinstance(catalog, dict):
        raise ValueError("统一价目数据格式无效")
    return catalog, row["revision"]


def current_catalog():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            return read_catalog(cur)[0]
    finally:
        conn.close()


def format_price(value):
    return f"{Decimal(str(value)).normalize():f}"


def delivery_label(catalog, venue_id):
    return f"{format_price(catalog['deliveryPrices'][str(venue_id)])}💲"


def delivery_name(catalog, venue_id):
    return f"西港{delivery_label(catalog, venue_id)}外送"
