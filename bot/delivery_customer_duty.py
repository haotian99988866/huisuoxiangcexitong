"""Delivery-specific eligibility for the shared customer duty subscription engine."""
from db import get_conn
from duty_shift import current_shift_window
from delivery_posts import public_person_media, attendance_publication
from delivery_staff import DELIVERY_VENUE_IDS


def eligible(member):
    return (member["venue_id"] in DELIVERY_VENUE_IDS and not member["deleted_at"]
            and member["merged_into_id"] is None and member["consent_status"] == "granted"
            and bool(member["public_media"]))


def member_row(cur, person_id, lock=False):
    cur.execute("SELECT p.*,v.name AS venue FROM delivery_people p "
                "JOIN venues v ON v.id=p.venue_id WHERE p.id=%s" +
                (" FOR UPDATE" if lock else ""), (person_id,))
    member = cur.fetchone()
    if not member:
        raise ValueError("外送人员档案不存在")
    cur.execute("SELECT * FROM delivery_media WHERE person_id=%s AND venue_id=%s "
                "AND media_type IN ('photo','video') ORDER BY message_id DESC,id DESC",
                (person_id, member["venue_id"]))
    member["channel_media"] = cur.fetchall()
    member["public_media"] = public_person_media(member["channel_media"])
    return member


def published_at(member, start, end, after=None):
    publication = attendance_publication(
        member["channel_media"], member["public_media"], start, end, current_shift_window(start)[2])
    return publication if publication and (after is None or publication > after) else None


def catalog_status(venue_id, user_id):
    from staff_customer_duty import state_result
    if type(venue_id) is not int or venue_id not in DELIVERY_VENUE_IDS:
        raise ValueError("请选择有效的外送分类")
    start, end, shift = current_shift_window()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT m.* FROM delivery_media m JOIN delivery_people p ON p.id=m.person_id "
                        "WHERE p.venue_id=%s AND m.venue_id=p.venue_id AND p.deleted_at IS NULL "
                        "AND p.merged_into_id IS NULL AND p.consent_status='granted' "
                        "AND m.media_type IN ('photo','video') ORDER BY m.message_id DESC,m.id DESC", (venue_id,))
            groups = {}
            for row in cur.fetchall():
                groups.setdefault(row["person_id"], []).append(row)
            cur.execute("SELECT s.person_id,s.enabled,s.revision FROM delivery_customer_subscriptions s "
                        "JOIN delivery_people p ON p.id=s.person_id WHERE p.venue_id=%s AND s.user_id=%s",
                        (venue_id, user_id))
            subscriptions = {row["person_id"]: row for row in cur.fetchall()}
            states = {}
            for person_id, rows in groups.items():
                media = public_person_media(rows)
                if media:
                    publication = attendance_publication(rows, media, start, end, shift)
                    states[str(person_id)] = state_result(subscriptions.get(person_id), True, publication, shift, end)
        return {"states": states, "shift_ends_at": int(end.timestamp() * 1000)}
    finally:
        conn.close()
