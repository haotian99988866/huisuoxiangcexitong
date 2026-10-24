"""给已入库、还没识别过年龄的外送照片补做识别：python backfill_delivery_age.py"""
import os
import sys

import requests
from dotenv import load_dotenv

from db import get_conn
from delivery_age import extract_age, ocr_image_text

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


def download(file_id):
    info = requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
        params={"file_id": file_id},
        timeout=30,
    ).json()
    if not info.get("ok"):
        return None
    response = requests.get(
        f"https://api.telegram.org/file/bot{BOT_TOKEN}/{info['result']['file_path']}",
        timeout=60,
    )
    return response.content if response.ok else None


def main():
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, file_id, caption FROM delivery_media "
            "WHERE media_type = 'photo' AND ocr_text IS NULL ORDER BY id"
        )
        rows = cur.fetchall()
    print(f"待识别 {len(rows)} 张")
    for row in rows:
        data = download(row["file_id"])
        if data is None:
            print(f"#{row['id']} 下载失败，跳过")
            continue
        text = ocr_image_text(data)
        age = extract_age(row["caption"], text)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE delivery_media SET ocr_text = %s, age = %s WHERE id = %s",
                (text, age, row["id"]),
            )
        conn.commit()
        print(f"#{row['id']} 年龄={age}")
    conn.close()


if __name__ == "__main__":
    sys.exit(main())
