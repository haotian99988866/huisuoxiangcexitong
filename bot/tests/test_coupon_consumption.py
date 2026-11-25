from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
import threading
import unittest
from unittest.mock import patch
import uuid

import pymysql

import coupon_campaign as campaign
import customer_records as records
import finance
from db import get_conn


class CouponConsumptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_conn()
        cls.database = "test_coupon_crm_" + uuid.uuid4().hex[:16]
        with cls.root.cursor() as cur:
            cur.execute(f"CREATE DATABASE `{cls.database}` CHARACTER SET utf8mb4")
        cls.addClassCleanup(cls.cleanup_database)

        def connect():
            return pymysql.connect(host=cls.root.host, port=cls.root.port, user=cls.root.user,
                                   password=cls.root.password, database=cls.database, charset="utf8mb4",
                                   cursorclass=pymysql.cursors.DictCursor)

        cls.connect = staticmethod(connect)
        for module in (campaign, records):
            patcher = patch.object(module, "get_conn", side_effect=connect)
            patcher.start()
            cls.addClassCleanup(patcher.stop)
        for name, result in (("ensure_tables", None), ("_ensure_defaults", None), ("commission_item", None)):
            patcher = patch.object(finance, name, return_value=result)
            patcher.start()
            cls.addClassCleanup(patcher.stop)
        records.ensure_tables()
        campaign.ensure_tables()
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE TABLE bot_users(tg_user_id BIGINT PRIMARY KEY,username VARCHAR(64),display_name VARCHAR(128))")
                cur.execute("CREATE TABLE analytics_customers(user_id BIGINT PRIMARY KEY,username VARCHAR(64),display_name VARCHAR(128))")
                cur.execute("CREATE TABLE venues(id INT PRIMARY KEY,name VARCHAR(128))")
            conn.commit()
        finally:
            conn.close()

    @classmethod
    def cleanup_database(cls):
        with cls.root.cursor() as cur:
            cur.execute(f"DROP DATABASE `{cls.database}`")
        cls.root.close()

    def sql(self, query, args=()):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(query, args)
                result = cur.fetchall() if cur.description else []
            conn.commit()
            return result
        finally:
            conn.close()

    def setUp(self):
        for table in ("coupon_redemption_audit", "coupon_invites", "coupon_vouchers", "crm_audit", "crm_consumptions",
                      "crm_customers", "bot_users", "analytics_customers", "venues"):
            self.sql(f"DELETE FROM {table}")
        self.now = datetime(2026, 3, 10, 12)
        for module in (records, campaign):
            patcher = patch.object(module, "_utcnow", return_value=self.now)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sql("INSERT INTO bot_users VALUES(42,'guest','Guest')")
        self.sql("""INSERT INTO crm_customers(user_id,telegram_user_id,alias,preferences,avoidances,notes,created_at,updated_at)
                    VALUES(-123,42,'guest','','','',%s,%s)""", (self.now, self.now))
        self.sql("INSERT INTO venues VALUES(1,'按摩'),(2,'会所'),(7,'外送')")

    def voucher(self, expires=None):
        voucher_id = uuid.uuid4().hex
        self.sql("INSERT INTO coupon_vouchers(id,owner_id,issued_at,expires_at) VALUES(%s,42,%s,%s)",
                 (voucher_id, self.now - timedelta(days=1), expires or self.now + timedelta(days=10)))
        return voucher_id

    def payload(self, original="30.00", venue=2, kind="venue"):
        data = {"action": "admin_consumption_save", "request_id": uuid.uuid4().hex,
                "user_id": -123, "revision": 0, "confirmed": True, "service_kind": kind,
                "venue_id": venue, "staff_id": None, "delivery_person_id": None,
                "service": "测试", "occurred_at": "2026-03-10T11:00:00Z",
                "amount": original, "refunded": "0", "note": "", "reason": ""}
        preview = campaign.preview({**data, "original_amount": original})
        data.update(original_amount=original, amount=preview["payable"], coupon_previewed=True,
                    coupon_expected_id=preview["coupon_id"])
        return data, preview

    def test_exact_value_is_zero_and_retry_does_not_duplicate(self):
        voucher = self.voucher()
        data, preview = self.payload("20")
        self.assertEqual(preview["coupon_id"], voucher)
        self.assertEqual(preview["payable"], "0.00")
        self.assertTrue(records.save(data, 9000)["ok"])
        self.assertTrue(records.save(data, 9000)["replayed"])
        self.assertEqual(self.sql("SELECT amount FROM crm_consumptions"), [{"amount": Decimal("0.00")}])
        self.assertEqual(self.sql("SELECT status,consumption_id FROM coupon_vouchers"),
                         [{"status": "redeemed", "consumption_id": data["request_id"]}])
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM coupon_redemption_audit")[0]["n"], 1)

    def test_earliest_expiry_is_chosen_only_once_and_delivery_works(self):
        later = self.voucher(self.now + timedelta(days=9))
        earlier = self.voucher(self.now + timedelta(days=2))
        data, preview = self.payload("50", 7, "delivery")
        self.assertEqual(preview["coupon_id"], earlier)
        self.assertEqual(preview["payable"], "30.00")
        records.save(data, 9000)
        rows = self.sql("SELECT id,status FROM coupon_vouchers ORDER BY expires_at")
        self.assertEqual(rows, [{"id": earlier, "status": "redeemed"}, {"id": later, "status": "available"}])

    def test_under_value_and_excluded_source_do_not_redeem(self):
        self.voucher()
        data, preview = self.payload("19.99")
        self.assertTrue(preview["manual_review"])
        self.assertIsNone(preview["coupon_id"])
        records.save(data, 9000)
        excluded, preview = self.payload("40", 1)
        self.assertIsNone(preview["coupon_id"])
        records.save(excluded, 9000)
        self.assertEqual(self.sql("SELECT status FROM coupon_vouchers"), [{"status": "available"}])

    def test_changed_coupon_and_old_client_are_rejected(self):
        voucher = self.voucher()
        data, _ = self.payload()
        self.sql("UPDATE coupon_vouchers SET status='redeemed' WHERE id=%s", (voucher,))
        with self.assertRaises(records.RecordConflict):
            records.save(data, 9000)
        self.sql("UPDATE coupon_vouchers SET status='available' WHERE id=%s", (voucher,))
        old, _ = self.payload()
        for key in ("original_amount", "coupon_previewed", "coupon_expected_id"):
            del old[key]
        old["amount"] = "30"
        with self.assertRaises(records.RecordConflict):
            records.save(old, 9000)
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM crm_consumptions")[0]["n"], 0)

    def test_expired_coupon_is_not_applied_and_expiry_after_preview_conflicts(self):
        voucher = self.voucher(self.now + timedelta(seconds=1))
        data, _ = self.payload()
        with patch.object(records, "_utcnow", return_value=self.now + timedelta(seconds=2)):
            with self.assertRaises(records.RecordConflict):
                records.save(data, 9000)
        with patch.object(campaign, "_utcnow", return_value=self.now + timedelta(seconds=2)):
            _, preview = self.payload()
        self.assertIsNone(preview["coupon_id"])
        self.assertEqual(self.sql("SELECT status FROM coupon_vouchers WHERE id=%s", (voucher,)),
                         [{"status": "available"}])

    def test_failure_after_redemption_rolls_back_everything(self):
        self.voucher()
        data, _ = self.payload()
        apply = campaign.apply_automatic_redemption

        def fail(*args):
            apply(*args)
            raise RuntimeError("simulated failure")

        with patch.object(campaign, "apply_automatic_redemption", side_effect=fail):
            with self.assertRaises(RuntimeError):
                records.save(data, 9000)
        self.assertEqual(self.sql("SELECT status FROM coupon_vouchers"), [{"status": "available"}])
        for table in ("crm_consumptions", "crm_audit", "coupon_redemption_audit"):
            self.assertEqual(self.sql(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"], 0)

    def test_two_confirmations_cannot_spend_the_same_coupon(self):
        self.voucher()
        first, _ = self.payload()
        second, _ = self.payload()

        def save(data):
            try:
                return records.save(data, 9000)
            except records.RecordConflict:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, (first, second)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM crm_consumptions")[0]["n"], 1)
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM coupon_redemption_audit")[0]["n"], 1)

    def test_concurrent_retry_of_same_request_returns_original_result(self):
        self.voucher()
        data, _ = self.payload()
        barrier = threading.Barrier(2)
        identity = records._identity

        def synchronize(cur, user_id):
            result = identity(cur, user_id)
            barrier.wait(timeout=10)
            return result

        with patch.object(records, "_identity", side_effect=synchronize):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: records.save(data, 9000), range(2)))
        self.assertEqual(sum(bool(result.get("replayed")) for result in results), 1)
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM crm_consumptions")[0]["n"], 1)
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM coupon_redemption_audit")[0]["n"], 1)

    def test_reverse_keeps_voucher_locked_until_original_payment_is_corrected(self):
        voucher = self.voucher()
        data, _ = self.payload()
        records.save(data, 9000)
        campaign.reverse_redemption(voucher, "核对收款", 9000, True, uuid.uuid4().hex)
        self.assertEqual(self.sql("SELECT status FROM coupon_vouchers"), [{"status": "reversal_pending"}])
        self.assertTrue(records.save(data, 9000)["replayed"])
        edit = {**data, "request_id": uuid.uuid4().hex, "record_id": data["request_id"],
                "revision": 1, "reason": "核对收款"}
        with self.assertRaises(ValueError):
            records.save(edit, 9000)
        edit["amount"] = "30"
        records.save(edit, 9000)
        self.assertEqual(self.sql("SELECT status,consumption_id FROM coupon_vouchers"),
                         [{"status": "available", "consumption_id": None}])

    def test_reverse_and_void_release_coupon_atomically(self):
        voucher = self.voucher()
        data, _ = self.payload()
        records.save(data, 9000)
        campaign.reverse_redemption(voucher, "消费登记错误", 9000, True, uuid.uuid4().hex)
        void = {"action": "admin_consumption_void", "request_id": uuid.uuid4().hex,
                "user_id": -123, "revision": 1, "confirmed": True,
                "record_id": data["request_id"], "reason": "消费登记错误"}
        records.save(void, 9000)
        self.assertEqual(self.sql("SELECT status FROM crm_consumptions"), [{"status": "void"}])
        self.assertEqual(self.sql("SELECT status,consumption_id FROM coupon_vouchers"),
                         [{"status": "available", "consumption_id": None}])

    def test_reverse_does_not_restore_coupon_when_late_exit_disqualifies_it(self):
        voucher = self.voucher()
        data, _ = self.payload()
        records.save(data, 9000)
        self.sql("""INSERT INTO coupon_invites
            (invitee_id,inviter_id,joined_at,left_at,state,voucher_id)
            VALUES(10,42,%s,%s,'review',%s),(11,42,%s,NULL,'qualified',%s)""",
                 (self.now - timedelta(days=8), self.now - timedelta(days=2), voucher,
                  self.now - timedelta(days=8), voucher))
        campaign.reverse_redemption(voucher, "退出需核对", 9000, True, uuid.uuid4().hex)
        void = {"action": "admin_consumption_void", "request_id": uuid.uuid4().hex,
                "user_id": -123, "revision": 1, "confirmed": True,
                "record_id": data["request_id"], "reason": "退出需核对"}
        records.save(void, 9000)
        self.assertEqual(self.sql("SELECT status FROM coupon_vouchers"), [{"status": "invalid"}])
        self.assertEqual(self.sql("SELECT state,voucher_id FROM coupon_invites WHERE invitee_id=11"),
                         [{"state": "qualified", "voucher_id": None}])
