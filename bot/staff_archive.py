"""Durable venue staff identities, distinct photos and daily duty snapshots."""
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import logging
import threading

from db import get_conn
from duty_shift import current_shift_window
from image_hash import distance, SAME_IMAGE_DISTANCE
from technician_photo_ocr import extract_labeled_technician_code, extract_technician_profile, extract_technician_category
from venue_pricing import staff_price, dihao_category_for_photo, mingxi_price_options

logger = logging.getLogger(__name__)
VENUE_IDS = frozenset({1, 2, 4, 5, 6})


def ensure_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for query in (
                """CREATE TABLE IF NOT EXISTS staff_members (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY, venue_id INT NOT NULL,
                    code VARCHAR(32) NOT NULL, active_from DATETIME NOT NULL,
                    ended_at DATETIME NULL, first_seen DATETIME NOT NULL, last_seen DATETIME NOT NULL,
                    blocked BOOLEAN NOT NULL DEFAULT FALSE,
                    adult_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
                    INDEX staff_code(venue_id,code,active_from)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
                """CREATE TABLE IF NOT EXISTS staff_slots (
                    venue_id INT NOT NULL, code VARCHAR(32) NOT NULL, staff_id BIGINT NOT NULL,
                    PRIMARY KEY(venue_id,code)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
                """CREATE TABLE IF NOT EXISTS staff_photos (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY, staff_id BIGINT NOT NULL,
                    file_id VARCHAR(255) NOT NULL, image_hash CHAR(16) NULL,
                    channel_id VARCHAR(64) NOT NULL, msg_id BIGINT NOT NULL,
                    first_seen DATETIME NOT NULL, last_seen DATETIME NOT NULL,
                    INDEX staff_photo_member(staff_id,id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
                """CREATE TABLE IF NOT EXISTS staff_photo_sources (
                    source_id BIGINT PRIMARY KEY, staff_id BIGINT NOT NULL, photo_id BIGINT NOT NULL
                ) ENGINE=InnoDB""",
                """CREATE TABLE IF NOT EXISTS staff_rosters (
                    source_id BIGINT PRIMARY KEY, venue_id INT NOT NULL,
                    duty_date DATE NOT NULL, shift VARCHAR(16) NOT NULL,
                    members_json JSON NOT NULL, fingerprint CHAR(64) NOT NULL,
                    published_at DATETIME NOT NULL, INDEX staff_roster_time(venue_id,published_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
                """CREATE TABLE IF NOT EXISTS staff_changes (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY, old_staff_id BIGINT NOT NULL UNIQUE,
                    new_staff_id BIGINT NOT NULL, actor_id BIGINT NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB""",
                """CREATE TABLE IF NOT EXISTS staff_archive_audit (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY, staff_id BIGINT NOT NULL,
                    actor_id BIGINT NOT NULL, action VARCHAR(32) NOT NULL,
                    description_length INT NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX staff_archive_actions(staff_id,id)
                ) ENGINE=InnoDB""",
            ):
                cur.execute(query)
            for name, definition in (
                ("price_tags_json", "JSON NULL"), ("price_tags_at", "DATETIME NULL"),
                ("description", "TEXT NULL"),
                ("description_revision", "INT NOT NULL DEFAULT 0"),
                ("deleted_at", "DATETIME NULL"),
            ):
                cur.execute(f"SHOW COLUMNS FROM staff_members LIKE '{name}'")
                if not cur.fetchone():
                    cur.execute(f"ALTER TABLE staff_members ADD COLUMN {name} {definition}")
        conn.commit()
    finally:
        conn.close()


def _person(cur, venue_id, code, seen_at):
    cur.execute(
        "SELECT * FROM staff_members WHERE venue_id=%s AND code=%s AND active_from<=%s "
        "AND (ended_at IS NULL OR ended_at>%s) ORDER BY active_from DESC,id DESC LIMIT 1",
        (venue_id, code, seen_at, seen_at))
    member = cur.fetchone()
    if member:
        cur.execute("UPDATE staff_members SET first_seen=LEAST(first_seen,%s),"
                    "last_seen=GREATEST(last_seen,%s) WHERE id=%s", (seen_at, seen_at, member["id"]))
        return member["id"]
    cur.execute(
        "INSERT INTO staff_members(venue_id,code,active_from,first_seen,last_seen) "
        "VALUES(%s,%s,'1970-01-01',%s,%s)", (venue_id, code, seen_at, seen_at))
    staff_id = cur.lastrowid
    cur.execute("INSERT INTO staff_slots(venue_id,code,staff_id) VALUES(%s,%s,%s)",
                (venue_id, code, staff_id))
    return staff_id


def _save_price_tags(cur, staff_id, tags, seen_at):
    if tags:
        cur.execute("UPDATE staff_members SET price_tags_json=%s,price_tags_at=%s WHERE id=%s "
                    "AND (price_tags_at IS NULL OR price_tags_at<=%s)",
                    (json.dumps(sorted(set(tags))), seen_at, staff_id, seen_at))


def synchronize(cur):
    placeholders = ",".join(["%s"] * len(VENUE_IDS))
    cur.execute(f"SELECT * FROM attendance_daily WHERE venue_id IN ({placeholders}) "
                "ORDER BY created_at,id", tuple(sorted(VENUE_IDS)))
    for row in cur.fetchall():
        digest = hashlib.sha256(
            f"{row['technician_codes']}|{row['duty_date']}|{row['duty_shift']}|{row['created_at']}".encode()
        ).hexdigest()
        cur.execute("SELECT fingerprint FROM staff_rosters WHERE source_id=%s", (row["id"],))
        saved = cur.fetchone()
        codes = [code.strip() for code in dict.fromkeys((row["technician_codes"] or "").split(",")) if code.strip()]
        ids = [_person(cur, row["venue_id"], code, row["created_at"]) for code in codes]
        cur.execute("SELECT group_name,technician_codes FROM attendance_groups "
                    "WHERE venue_id=%s AND duty_date=%s AND group_type='roster'",
                    (row["venue_id"], row["duty_date"]))
        groups = cur.fetchall()
        cur.execute("SELECT si.category,st.technician_code FROM service_item_technicians st "
                    "JOIN service_items si ON si.id=st.service_item_id WHERE si.venue_id=%s AND st.duty_date=%s",
                    (row["venue_id"], row["duty_date"]))
        mappings = cur.fetchall()
        for code, staff_id in zip(codes, ids):
            tags = ["board:" + group["group_name"] for group in groups
                    if code in (group["technician_codes"] or "").split(",")]
            tags.extend("service:" + item["category"] for item in mappings if item["technician_code"] == code)
            _save_price_tags(cur, staff_id, tags, row["created_at"])
        if saved and saved["fingerprint"] == digest:
            continue
        shift = row["duty_shift"]
        if shift in {"早班", "白班"}:
            shift = "白班"
        elif shift != "晚班":
            shift = current_shift_window(row["created_at"])[2]
        cur.execute(
            "INSERT INTO staff_rosters(source_id,venue_id,duty_date,shift,members_json,fingerprint,published_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
            "duty_date=VALUES(duty_date),shift=VALUES(shift),members_json=VALUES(members_json),"
            "fingerprint=VALUES(fingerprint),published_at=VALUES(published_at)",
            (row["id"], row["venue_id"], row["duty_date"], shift, json.dumps(ids), digest, row["created_at"]))
    cur.execute(f"SELECT * FROM technician_photos WHERE venue_id IN ({placeholders}) "
                "ORDER BY created_at,id", tuple(sorted(VENUE_IDS)))
    rows = cur.fetchall()
    for row in rows:
        code = row["technician_code"]
        if row["venue_id"] in {4, 5, 6}:
            code = code or extract_labeled_technician_code(row["ocr_text"])
        if not code or not row["file_id"] or row["status"] not in {"matched", "pending_review"}:
            continue
        if row["venue_id"] not in {4, 5, 6} and row["status"] != "matched":
            continue
        cur.execute("SELECT staff_id,photo_id FROM staff_photo_sources WHERE source_id=%s", (row["id"],))
        saved = cur.fetchone()
        staff_id = saved["staff_id"] if saved else _person(cur, row["venue_id"], code, row["created_at"])
        profile = extract_technician_profile([row["ocr_text"] or ""], code)
        category = (extract_technician_category(row["ocr_text"]) if row["venue_id"] == 5
                    else dihao_category_for_photo(code, row["ocr_text"]) if row["venue_id"] == 6 else None)
        if category:
            _save_price_tags(cur, staff_id, ["category:" + category], row["created_at"])
        age = profile.get("age")
        if age and age.isdigit():
            cur.execute(
                "UPDATE staff_members SET blocked=blocked OR %s,adult_confirmed=adult_confirmed OR %s WHERE id=%s",
                (int(age) < 18, int(age) >= 18, staff_id))
        if saved:
            continue
        cur.execute("SELECT blocked,adult_confirmed FROM staff_members WHERE id=%s", (staff_id,))
        eligibility = cur.fetchone()
        if eligibility["blocked"] or (row["venue_id"] == 6 and not eligibility["adult_confirmed"]):
            continue
        cur.execute("SELECT id,file_id,image_hash FROM staff_photos WHERE staff_id=%s ORDER BY id", (staff_id,))
        photo_id = None
        for known in cur.fetchall():
            if known["file_id"] == row["file_id"] or (
                    known["image_hash"] and row["image_hash"]
                    and distance(known["image_hash"], row["image_hash"]) <= SAME_IMAGE_DISTANCE):
                photo_id = known["id"]
                break
        if photo_id is None:
            cur.execute(
                "INSERT INTO staff_photos(staff_id,file_id,image_hash,channel_id,msg_id,first_seen,last_seen) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s)",
                (staff_id, row["file_id"], row["image_hash"], row["channel_id"], row["msg_id"],
                 row["created_at"], row["created_at"]))
            photo_id = cur.lastrowid
        else:
            cur.execute("UPDATE staff_photos SET last_seen=GREATEST(last_seen,%s) WHERE id=%s",
                        (row["created_at"], photo_id))
        cur.execute("INSERT INTO staff_photo_sources(source_id,staff_id,photo_id) VALUES(%s,%s,%s)",
                    (row["id"], staff_id, photo_id))


def sync():
    conn = get_conn()
    locked = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK('venue_staff_archive',15) AS acquired")
            locked = bool(cur.fetchone()["acquired"])
            if not locked:
                raise RuntimeError("Staff archive synchronization lock unavailable")
            synchronize(cur)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if locked:
            with conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK('venue_staff_archive')")
        conn.close()


def _venue(value):
    if type(value) is not int or value not in VENUE_IDS:
        raise ValueError("请选择支持人员档案的会所，外送独立管理")
    return value


def listing(data, admin=False):
    venue_id = _venue(data.get("venue_id"))
    mode = data.get("mode", "duty")
    page = data.get("page", 1)
    if mode not in {"duty", "all"} or type(page) is not int or page < 1 or page > 10000:
        raise ValueError("档案筛选或分页无效")
    now = datetime.now()
    start, end, shift = current_shift_window(now)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT members_json,shift FROM staff_rosters WHERE venue_id=%s "
                "AND published_at>=%s AND published_at<%s ORDER BY published_at DESC,source_id DESC LIMIT 1",
                (venue_id, start, end))
            roster = cur.fetchone()
            duty_ids = json.loads(roster["members_json"]) if roster else []
            photo_duty_ids = []
            if venue_id in {4, 5, 6}:
                cur.execute(
                    "SELECT DISTINCT p.staff_id FROM staff_photos p JOIN staff_members m ON m.id=p.staff_id "
                    "WHERE m.venue_id=%s AND p.last_seen>=%s AND p.last_seen<%s",
                    (venue_id, start, end))
                photo_duty_ids = [row["staff_id"] for row in cur.fetchall()]
                duty_ids = list(dict.fromkeys([*duty_ids, *photo_duty_ids]))
            where = "m.venue_id=%s AND m.ended_at IS NULL AND m.deleted_at IS NULL"
            args = [venue_id]
            if not admin:
                where += " AND m.blocked=0 AND (m.venue_id<>6 OR m.adult_confirmed=1)"
            if mode == "duty":
                where += " AND m.id IN (" + ",".join(["%s"] * max(len(duty_ids), 1)) + ")"
                args.extend(duty_ids or [0])
            cur.execute(f"SELECT COUNT(*) AS total FROM staff_members m WHERE {where}", args)
            total = cur.fetchone()["total"]
            cur.execute(
                "SELECT m.id,m.code,m.first_seen,m.last_seen,m.blocked,m.price_tags_json,"
                "(SELECT MAX(r.published_at) FROM staff_rosters r WHERE r.venue_id=m.venue_id "
                "AND JSON_CONTAINS(r.members_json,CAST(m.id AS JSON))) AS last_roster_at,"
                "(SELECT MAX(p.last_seen) FROM staff_photos p WHERE p.staff_id=m.id) AS last_photo_at,"
                "(SELECT p.id FROM staff_photos p WHERE p.staff_id=m.id ORDER BY p.last_seen DESC,p.id DESC LIMIT 1) AS cover,"
                "(SELECT COUNT(*) FROM staff_photos p WHERE p.staff_id=m.id) AS photo_count "
                f"FROM staff_members m WHERE {where} ORDER BY m.id DESC", args)
            members = cur.fetchall()
            cur.execute("SELECT category,name,price FROM service_items WHERE venue_id=%s", (venue_id,))
            services = cur.fetchall()
            for row in members:
                tags = json.loads(row.pop("price_tags_json") or "[]")
                price, price_detail = staff_price(venue_id, row["code"], tags, services)
                row["price_usd"] = str(price) if price is not None else None
                row["price_detail"] = price_detail
                if venue_id == 2:
                    row["price_options"] = mingxi_price_options(tags)
                label = roster["shift"] if roster and row["id"] not in photo_duty_ids else shift
                row["duty_label"] = f"{label}出勤" if row["id"] in duty_ids else None
                row["duty_source"] = "photo" if row["id"] in photo_duty_ids else "roster" if row["id"] in duty_ids else None
                last_duty = row.pop("last_roster_at")
                last_photo = row.pop("last_photo_at")
                if venue_id in {4, 5, 6} and last_photo and (not last_duty or last_photo > last_duty):
                    last_duty = last_photo
                days = max(0, (now.date() - last_duty.date()).days) if last_duty else None
                row["absence_days"] = None if row["duty_label"] else days
                row["attendance_overlay"] = row["duty_label"] or (
                    "出勤记录待确认" if days is None else
                    "今日出勤待确认" if days == 0 else
                    "长期未出勤" if days >= 60 else f"{days}天未出勤")
                row["last_duty_at"] = str(last_duty) if last_duty else None
                row["first_seen"] = str(row["first_seen"])
                row["last_seen"] = str(row["last_seen"])
            members.sort(key=lambda member: (
                member["price_usd"] is not None,
                Decimal(member["price_usd"]) if member["price_usd"] is not None else Decimal("0"),
                member["id"]), reverse=True)
        return {"members": members, "total": total, "shift": shift,
                "roster_available": bool(roster or photo_duty_ids), "venue_id": venue_id}
    finally:
        conn.close()


def detail(data, admin=False):
    staff_id = data.get("staff_id")
    if type(staff_id) is not int or staff_id <= 0:
        raise ValueError("人员档案编号无效")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            private_fields = ",description,description_revision" if admin else ""
            cur.execute("SELECT id,venue_id,code,ended_at,blocked,adult_confirmed" + private_fields +
                        " FROM staff_members WHERE id=%s AND deleted_at IS NULL", (staff_id,))
            row = cur.fetchone()
            if not row or (not admin and (row["ended_at"] or row["blocked"]
                    or (row["venue_id"] == 6 and not row["adult_confirmed"]))):
                return None
            cur.execute("SELECT id,first_seen FROM staff_photos WHERE staff_id=%s ORDER BY last_seen DESC,id DESC", (staff_id,))
            row["photos"] = [{**p, "first_seen": str(p["first_seen"])} for p in cur.fetchall()]
            row["ended_at"] = str(row["ended_at"]) if row["ended_at"] else None
            if admin:
                row["description"] = row["description"] or ""
            return row
    finally:
        conn.close()


def photo(photo_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.file_id,m.venue_id FROM staff_photos p JOIN staff_members m ON m.id=p.staff_id "
                "WHERE p.id=%s AND m.ended_at IS NULL AND m.deleted_at IS NULL AND m.blocked=0 "
                "AND (m.venue_id<>6 OR m.adult_confirmed=1)", (photo_id,))
            return cur.fetchone()
    finally:
        conn.close()


def update_archive(data, actor_id, *, delete=False):
    from customer_records import RecordConflict
    staff_id = data.get("staff_id")
    if type(staff_id) is not int or not 0 < staff_id <= 9007199254740991:
        raise ValueError("人员档案编号无效")
    if data.get("confirmed") is not True:
        raise ValueError("请核对并确认档案操作")
    description = data.get("description")
    revision = data.get("description_revision")
    if not delete:
        if not isinstance(description, str) or len(description) > 2000:
            raise ValueError("说明须为不超过2000字的文字")
        if type(revision) is not int or revision < 0:
            raise ValueError("说明版本无效，请关闭后重新打开档案")
        description = description.strip()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id,ended_at,deleted_at,description_revision FROM staff_members "
                        "WHERE id=%s FOR UPDATE", (staff_id,))
            member = cur.fetchone()
            if not member:
                raise ValueError("人员档案不存在")
            if delete and member["deleted_at"]:
                conn.rollback()
                return {"ok": True, "staff_id": staff_id}
            if member["ended_at"] or member["deleted_at"]:
                raise RecordConflict("档案已删除或工号已换人，请刷新核对")
            if delete:
                # Keep the identity interval and source links so synchronization cannot recreate it.
                cur.execute("UPDATE staff_members SET deleted_at=%s,description=NULL,"
                            "description_revision=description_revision+1 WHERE id=%s",
                            (datetime.now(), staff_id))
                cur.execute("UPDATE staff_duty_subscriptions SET enabled=0 WHERE staff_id=%s", (staff_id,))
                cur.execute("UPDATE staff_customer_subscriptions SET enabled=0,revision=revision+1 "
                            "WHERE staff_id=%s AND enabled=1", (staff_id,))
                for table in ("staff_duty_notifications", "staff_customer_notifications"):
                    cur.execute(f"UPDATE {table} SET status='cancelled' WHERE staff_id=%s AND status='pending'",
                                (staff_id,))
            else:
                if revision != member["description_revision"]:
                    raise RecordConflict("说明已被修改，请关闭后重新打开核对")
                cur.execute("UPDATE staff_members SET description=%s,"
                            "description_revision=description_revision+1 WHERE id=%s",
                            (description, staff_id))
            cur.execute("INSERT INTO staff_archive_audit(staff_id,actor_id,action,description_length) "
                        "VALUES(%s,%s,%s,%s)", (
                            staff_id, actor_id, "deleted" if delete else "description_saved",
                            0 if delete else len(description)))
        conn.commit()
        return {"ok": True, "staff_id": staff_id} if delete else {
            "ok": True, "staff_id": staff_id, "description": description,
            "description_revision": revision + 1}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def replace(data, actor_id):
    from visit_stats import admin_ids
    if actor_id not in admin_ids():
        raise ValueError("仅平台管理员可更换人员归属")
    staff_id = data.get("staff_id")
    if type(staff_id) is not int or staff_id <= 0 or data.get("confirmed") is not True:
        raise ValueError("请明确确认该工号已换成新人")
    conn = get_conn()
    locked = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK('venue_staff_archive',15) AS acquired")
            locked = bool(cur.fetchone()["acquired"])
            if not locked:
                raise RuntimeError("Staff archive change lock unavailable")
            synchronize(cur)
            cur.execute("SELECT new_staff_id FROM staff_changes WHERE old_staff_id=%s", (staff_id,))
            existing = cur.fetchone()
            if existing:
                conn.rollback()
                return {"staff_id": existing["new_staff_id"]}
            cur.execute("SELECT * FROM staff_members WHERE id=%s FOR UPDATE", (staff_id,))
            old = cur.fetchone()
            if not old or old["ended_at"] or old.get("deleted_at"):
                raise ValueError("人员档案不存在或已停用，请刷新核对")
            now = datetime.now()
            cur.execute("UPDATE staff_members SET ended_at=%s WHERE id=%s", (now, staff_id))
            cur.execute(
                "INSERT INTO staff_members(venue_id,code,active_from,first_seen,last_seen) VALUES(%s,%s,%s,%s,%s)",
                (old["venue_id"], old["code"], now, now, now))
            new_id = cur.lastrowid
            cur.execute("UPDATE staff_slots SET staff_id=%s WHERE venue_id=%s AND code=%s",
                        (new_id, old["venue_id"], old["code"]))
            cur.execute("INSERT INTO staff_changes(old_staff_id,new_staff_id,actor_id) VALUES(%s,%s,%s)",
                        (staff_id, new_id, actor_id))
        conn.commit()
        return {"staff_id": new_id}
    except Exception:
        conn.rollback()
        raise
    finally:
        if locked:
            with conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK('venue_staff_archive')")
        conn.close()


def start_worker():
    def run():
        while True:
            try:
                sync()
            except Exception:
                logger.exception("Staff archive synchronization failed; source records retained until successful sync")
            threading.Event().wait(15)
    threading.Thread(target=run, name="staff-archive", daemon=True).start()
