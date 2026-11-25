import argparse

from db import get_conn
from technician_photo_ocr import rematch_pending_by_labeled_code, review_pending_photo


def list_pending():
    conn = get_conn()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT p.id, p.venue_id, v.name AS venue_name, p.duty_date,
                   p.candidate_codes, p.ocr_confidence, p.ocr_text,
                   p.ocr_error, p.channel_id, p.msg_id
            FROM technician_photos p
            JOIN venues v ON v.id = p.venue_id
            WHERE p.status = 'pending_review'
            ORDER BY p.created_at, p.id
            """
        )
        photos = cur.fetchall()
    finally:
        if cur is not None:
            cur.close()
        conn.close()

    if not photos:
        print("当前没有待核对照片。")
        return

    for photo in photos:
        print(
            f"照片ID={photo['id']} | 会所={photo['venue_name']} "
            f"(ID {photo['venue_id']}) | 日期={photo['duty_date']} "
            f"| OCR候选={photo['candidate_codes'] or '无'} "
            f"| 置信度={photo['ocr_confidence'] or '无'}"
        )
        channel_id = str(photo["channel_id"])
        channel_numeric = (
            channel_id[4:] if channel_id.startswith("-100") else channel_id.lstrip("-")
        )
        print(f"  Telegram 消息：https://t.me/c/{channel_numeric}/{photo['msg_id']}")
        print(f"  OCR文本：{(photo['ocr_text'] or '').replace(chr(10), ' | ')}")
        if photo["ocr_error"]:
            print(f"  OCR错误：{photo['ocr_error']}")


def main():
    parser = argparse.ArgumentParser(
        description="列出待核对技师照片，或将照片与出勤工号人工确认关联。"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="列出待核对照片")
    approve_parser = commands.add_parser("approve", help="确认一张照片对应的工号")
    approve_parser.add_argument("photo_id", type=int)
    approve_parser.add_argument("technician_code")
    approve_parser.add_argument(
        "--archive",
        action="store_true",
        help="不校验当班名单，直接 matched 写入人员档案（日韩专场等）",
    )
    auto_parser = commands.add_parser(
        "auto-label",
        help="凯迪/五象/帝豪：OCR 文含明确工号标签的 pending 自动升 matched",
    )
    auto_parser.add_argument(
        "--venues",
        default="4,5,6",
        help="会所 ID，逗号分隔，默认 4,5,6",
    )
    args = parser.parse_args()

    if args.command == "list":
        list_pending()
        return

    if args.command == "auto-label":
        venue_ids = tuple(
            int(part.strip()) for part in str(args.venues).split(",") if part.strip()
        )
        count = rematch_pending_by_labeled_code(venue_ids)
        print(f"已按工号标签自动匹配 {count} 张（venues={venue_ids}）")
        return

    if review_pending_photo(
        args.photo_id, args.technician_code, require_roster=not args.archive
    ):
        mode = "档案核准" if args.archive else "名单核准"
        print(f"已确认（{mode}）：照片 {args.photo_id} -> 工号 {args.technician_code}")
    else:
        parser.error(f"照片 {args.photo_id} 不存在或已处理")


if __name__ == "__main__":
    main()
