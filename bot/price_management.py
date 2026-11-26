"""Administrator price and personal commission maintenance."""

import json
import os
from decimal import Decimal

import finance
from customer_records import RecordConflict
from db import get_conn
from price_catalog import delivery_name, read_catalog

_CLUB_KEYS = {4: "kaidiTechnicianCategories", 5: "atlanticTechnicianCategories",
              6: "dihaoTechnicianCategories"}
_MASSAGE_NAMES = {
    "small-services": "沐足 · 肩颈 · 头部 · 采耳 · 火罐 · 刮痧 · 洗耳 · 洗面 · 修脚 · 治脚气",
    "small-special": "甲沟炎 · 中耳炎 · 刺络 · 面部护理",
}


def _money_text(value):
    return None if value is None else str(Decimal(str(value)).quantize(Decimal("0.01")))


def _target(catalog, item, services):
    venue, key, name = item["venue_id"], item["service_key"], item["service_name"]
    if venue == 1:
        target_name = _MASSAGE_NAMES.get(key, name)
        matches = [row for row in services if row["name"] == target_name]
        if len(matches) != 1:
            raise ValueError(f"按摩项目无法唯一匹配：{name}，请先核实")
        return matches[0], "price"
    if venue == 2:
        parts = name.split("·")
        if len(parts) != 2 or parts[1] not in {"会员", "半价"}:
            raise ValueError(f"价目项目无法匹配：{name}")
        matches = [row for row in catalog["mingxiBoardPrices"] if row["board"] == parts[0]]
        if len(matches) != 1:
            raise ValueError(f"价目项目无法唯一匹配：{name}")
        return matches[0], "member" if parts[1] == "会员" else "half"
    if venue in _CLUB_KEYS:
        matches = [row for row in catalog[_CLUB_KEYS[venue]] if row["name"] == name]
        if len(matches) != 1:
            raise ValueError(f"价目项目无法唯一匹配：{name}")
        return matches[0], "price"
    if venue in (7, 8, 9) and key == "delivery-order":
        return catalog["deliveryPrices"], str(venue)
    raise ValueError("价目项目不受支持")


def _items(cur, owner_id, catalog, lock=False):
    suffix = " FOR UPDATE" if lock else ""
    cur.execute("""SELECT i.venue_id,v.name AS venue_name,i.service_key,i.service_name,
                          i.commission_amount
                   FROM finance_commission_items i JOIN venues v ON v.id=i.venue_id
                   WHERE i.owner_id=%s AND i.venue_id IN (1,2,4,5,6,7,8,9)
                         AND v.status='active'
                   ORDER BY i.venue_id,i.service_name""" + suffix, (owner_id,))
    items = cur.fetchall()
    cur.execute("SELECT id,name,price FROM service_items WHERE venue_id=1" + suffix)
    services = cur.fetchall()
    result = []
    for item in items:
        target, field = _target(catalog, item, services)
        result.append({
            **item,
            "venue_name": (delivery_name(catalog, item["venue_id"]) if item["venue_id"] in (7, 8, 9)
                           else item["venue_name"]),
            "price": _money_text(target.get(field)),
            "commission_amount": _money_text(item["commission_amount"]),
        })
    return result, services


def overview(owner_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            finance._ensure_defaults(cur, owner_id)
            catalog, revision = read_catalog(cur)
            items, _services = _items(cur, owner_id, catalog)
            cur.execute("""SELECT revision,venue_id,status,reason,created_at
                           FROM venue_price_channel_review
                           ORDER BY revision DESC,venue_id LIMIT 30""")
            tasks = [{**row, "created_at": row["created_at"].isoformat() + "Z"}
                     for row in cur.fetchall()]
        conn.commit()
        return {"revision": revision, "items": items, "channel_sync": tasks}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def retry_sync(data):
    if os.getenv("VENUE_PRICE_SAVE_ENABLED") != "1":
        raise ValueError("价目与提成保存暂未开放，频道价格同步仍在完善")
    revision, venue_id = data.get("revision"), data.get("venue_id")
    if (type(revision) is not int or revision < 1 or type(venue_id) is not int
            or venue_id not in (1, 2, 4, 5, 6)):
        raise ValueError("频道同步任务无效")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""UPDATE venue_price_channel_review SET status='pending',reason='等待频道同步'
                           WHERE revision=%s AND venue_id=%s AND status='failed'""",
                        (revision, venue_id))
            if cur.rowcount != 1:
                raise RecordConflict("同步任务状态已变化，请刷新后查看")
        conn.commit()
        return {"ok": True}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save(data, owner_id):
    if os.getenv("VENUE_PRICE_SAVE_ENABLED") != "1":
        raise ValueError("价目与提成保存暂未开放，频道价格同步仍在完善")
    revision = data.get("revision")
    changes = data.get("changes")
    if type(revision) is not int or revision < 1:
        raise ValueError("价目版本无效，请刷新后重试")
    if data.get("confirmed") is not True:
        raise ValueError("请核对并确认价格和提成变更")
    if not isinstance(changes, list) or not changes or len(changes) > 100:
        raise ValueError("请选择需要保存的价目")
    parsed = {}
    for change in changes:
        if not isinstance(change, dict) or type(change.get("venue_id")) is not int:
            raise ValueError("价目项目无效")
        key = (change["venue_id"], change.get("service_key"))
        if not isinstance(key[1], str) or key in parsed:
            raise ValueError("价目项目重复或无效")
        if not set(change).issubset({"venue_id", "service_key", "old_price",
                                     "old_commission", "price", "commission"}):
            raise ValueError("价目字段无效")
        if "price" not in change and "commission" not in change:
            raise ValueError("没有需要保存的变更")
        price = finance._money(change["price"], "价格") if "price" in change else None
        commission = (finance._money(change["commission"], "固定提成", allow_zero=True)
                      if "commission" in change else None)
        parsed[key] = (change, price, commission)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            catalog, current_revision = read_catalog(cur, lock=True)
            if revision != current_revision:
                raise RecordConflict("价目已被修改，请刷新后重新核对")
            finance._ensure_defaults(cur, owner_id)
            items, services = _items(cur, owner_id, catalog, lock=True)
            by_key = {(row["venue_id"], row["service_key"]): row for row in items}
            if not set(parsed).issubset(by_key):
                raise ValueError("价目项目不存在或已停用")
            actual_changes = []
            channel_sync_venues = set()
            now = finance._now_utc()
            next_revision = revision + 1
            for key, (change, price, commission) in parsed.items():
                item = by_key[key]
                if (change.get("old_price") != item["price"] or
                        change.get("old_commission") != item["commission_amount"]):
                    raise RecordConflict("价格或个人提成已变化，请刷新后重新核对")
                old_price = Decimal(item["price"]) if item["price"] is not None else None
                old_commission = Decimal(item["commission_amount"])
                new_price = price if price is not None else old_price
                new_commission = commission if commission is not None else old_commission
                if old_price == new_price and old_commission == new_commission:
                    continue
                if price is not None and price != old_price:
                    if key[0] in (1, 2, 4, 5, 6):
                        channel_sync_venues.add(key[0])
                    target, field = _target(catalog, item, services)
                    if key[0] == 1:
                        cur.execute("UPDATE service_items SET price=%s WHERE id=%s AND venue_id=1",
                                    (price, target["id"]))
                    else:
                        target[field] = str(price)
                if commission is not None and commission != old_commission:
                    cur.execute("""UPDATE finance_commission_items
                                   SET commission_amount=%s,updated_by=%s,updated_at=%s
                                   WHERE owner_id=%s AND venue_id=%s AND service_key=%s""",
                                (commission, owner_id, now, owner_id, *key))
                cur.execute("""INSERT INTO venue_price_audit
                               (revision,actor_id,venue_id,service_key,old_price,new_price,
                                old_commission,new_commission,changed_at)
                               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (next_revision, owner_id, *key, old_price, new_price,
                             old_commission, new_commission, now))
                actual_changes.append(key)
            if not actual_changes:
                raise ValueError("没有需要保存的变更")
            cur.execute("UPDATE venue_price_catalog SET revision=%s,catalog_json=%s WHERE id=1",
                        (next_revision, json.dumps(catalog, ensure_ascii=False)))
            for venue_id in sorted(channel_sync_venues):
                cur.execute("""INSERT INTO venue_price_channel_review
                               (revision,venue_id,status,reason,created_at)
                               VALUES(%s,%s,'pending',%s,%s)""",
                            (next_revision, venue_id, "等待频道同步", now))
        conn.commit()
        return {"ok": True, "revision": next_revision, "channel_sync_venues": sorted(channel_sync_venues)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
