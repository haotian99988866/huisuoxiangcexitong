from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

import pymysql
import requests
from telebot import apihelper

import coupon_campaign as campaign
import coupon_bot
import api_server
from customer_records import RecordConflict
from db import get_conn
from tests.test_admin_console import signed


def update(number, user_id, before, after, when, link=None):
    user = SimpleNamespace(id=user_id, is_bot=False, first_name=f"Guest {user_id}", last_name=None,
                           username=f"guest{user_id}")
    event = SimpleNamespace(
        chat=SimpleNamespace(id=campaign.CHANNEL_ID), date=int(when.replace(tzinfo=timezone.utc).timestamp()),
        old_chat_member=SimpleNamespace(status=before, user=user),
        new_chat_member=SimpleNamespace(status=after, user=user),
        invite_link=SimpleNamespace(invite_link=link) if link else None,
    )
    return SimpleNamespace(update_id=number, chat_member=event, my_chat_member=None)


class CouponCampaignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_conn()
        cls.database = "test_coupon_" + uuid.uuid4().hex[:16]
        with cls.root.cursor() as cur:
            cur.execute(f"CREATE DATABASE `{cls.database}` CHARACTER SET utf8mb4")
        cls.addClassCleanup(cls.cleanup_database)

        def connect():
            return pymysql.connect(host=cls.root.host, port=cls.root.port, user=cls.root.user,
                                   password=cls.root.password, database=cls.database, charset="utf8mb4",
                                   cursorclass=pymysql.cursors.DictCursor)

        cls.connect = staticmethod(connect)
        patcher = patch.object(campaign, "get_conn", side_effect=connect)
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        campaign.ensure_tables()
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE TABLE crm_customers(user_id BIGINT PRIMARY KEY, telegram_user_id BIGINT)")
                cur.execute("""CREATE TABLE crm_consumptions (
                    id CHAR(32) PRIMARY KEY,user_id BIGINT NOT NULL,venue_id INT,service_kind VARCHAR(16),
                    amount DECIMAL(12,2),refunded DECIMAL(12,2),status VARCHAR(16),occurred_at DATETIME,
                    service VARCHAR(128))""")
                cur.execute("CREATE TABLE venues (id INT PRIMARY KEY,name VARCHAR(128))")
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
        for table in ("coupon_operations", "coupon_redemption_requests", "coupon_redemption_audit", "coupon_notifications", "coupon_events", "coupon_invites", "coupon_invite_links", "coupon_vouchers",
                      "crm_consumptions", "crm_customers", "venues"):
            self.sql(f"DELETE FROM {table}")
        self.start = datetime(2026, 1, 1)
        self.sql("UPDATE coupon_campaign_state SET enabled=TRUE,enabled_at=%s,last_poll=%s,caught_up=TRUE,request_after=NULL",
                 (self.start, self.start))
        self.sql("INSERT INTO coupon_invite_links(inviter_id,invite_link,status,created_at) VALUES(100,'https://t.me/+test','ready',%s)",
                 (self.start,))

    def test_calendar_expiry_uses_local_month_and_clamps_end_of_month(self):
        self.assertEqual(campaign.expiry(datetime(2026, 1, 31, 12)), datetime(2026, 2, 28, 12))
        self.assertEqual(campaign.expiry(datetime(2026, 1, 31, 18)), datetime(2026, 2, 28, 18))
        self.assertEqual(campaign.expiry(datetime(2026, 12, 31, 12)), datetime(2027, 1, 31, 12))

    def test_join_leave_and_duplicate_updates_preserve_actual_exit_time(self):
        joined = self.start + timedelta(hours=1)
        left = joined + timedelta(days=6)
        join = update(1, 42, "left", "member", joined, "https://t.me/+test")
        leave = update(2, 42, "member", "left", left)
        campaign.record_updates([join], joined)
        self.sql("UPDATE coupon_campaign_state SET last_poll=%s", (left,))
        campaign.record_updates([leave], left)
        campaign.record_updates([join, leave], left)
        self.assertEqual(self.sql("SELECT state,left_at FROM coupon_invites"),
                         [{"state": "left", "left_at": left}])
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM coupon_events")[0]["n"], 2)

    def test_join_notification_is_once_per_first_invitation_and_not_a_reward(self):
        joined = self.start + timedelta(hours=1)
        join = update(1, 42, "left", "member", joined, "https://t.me/+test")
        campaign.record_updates([join, join], joined)
        campaign.record_updates([update(2, 42, "left", "member", joined, "https://t.me/+test")], joined)
        self.assertEqual(self.sql("SELECT event_key FROM coupon_notifications"), [{"event_key": "join_1"}])
        bot = Mock()
        with patch.object(campaign, "_utcnow", return_value=joined):
            self.assertTrue(campaign.notify_one(bot))
        self.assertEqual(bot.send_message.call_args.args[0], 100)
        self.assertIn("Guest 42", bot.send_message.call_args.args[1])
        self.assertIn("连续留存7天", bot.send_message.call_args.args[1])
        self.assertEqual(self.sql("SELECT state FROM coupon_invites"), [{"state": "pending"}])
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM coupon_vouchers")[0]["n"], 0)

    def test_five_verified_members_produce_one_voucher_without_duplicate_rewards(self):
        joined = self.start + timedelta(hours=1)
        events = [update(index, index + 10, "left", "member", joined, "https://t.me/+test")
                  for index in range(1, 6)]
        campaign.record_updates(events, joined)
        due = joined + timedelta(days=7)
        self.sql("UPDATE coupon_campaign_state SET last_poll=%s", (due,))
        bot = Mock()
        bot.get_chat_member.return_value = SimpleNamespace(status="member")
        with patch.object(campaign, "_utcnow", return_value=due - timedelta(seconds=1)):
            self.assertFalse(campaign.verify_one(bot))
        for index in range(5):
            check_time = due + timedelta(seconds=index * 6)
            self.sql("UPDATE coupon_campaign_state SET last_poll=%s", (check_time,))
            with patch.object(campaign, "_utcnow", return_value=check_time):
                self.assertTrue(campaign.verify_one(bot))
        voucher = campaign.issue_ready(due + timedelta(seconds=24))
        self.assertIsNotNone(voucher)
        self.assertIsNone(campaign.issue_ready(due + timedelta(seconds=24)))
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM coupon_vouchers")[0]["n"], 1)
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM coupon_invites WHERE voucher_id=%s", (voucher,))[0]["n"], 5)

    def test_unobserved_gap_requires_manual_review(self):
        joined = self.start + timedelta(hours=1)
        campaign.record_updates([update(1, 42, "left", "member", joined, "https://t.me/+test")], joined)
        resume = joined + timedelta(hours=24)
        campaign.record_updates([], resume)
        self.assertEqual(self.sql("SELECT state FROM coupon_invites"), [{"state": "review"}])
        with patch.object(campaign, "_utcnow", return_value=resume):
            self.assertFalse(campaign.verify_one(Mock()))

    def test_all_batches_of_uncertain_backlog_require_review(self):
        resume = self.start + timedelta(hours=24)
        first = [update(i, i + 100, "left", "member", self.start + timedelta(hours=1),
                        "https://t.me/+test") for i in range(1, 101)]
        campaign.record_updates(first, resume)
        campaign.record_updates([update(101, 201, "left", "member",
                                        self.start + timedelta(hours=2), "https://t.me/+test")], resume)
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM coupon_invites WHERE state='review'")[0]["n"], 101)

    def test_create_link_does_not_repeat_an_unknown_result(self):
        bot = Mock()
        bot.get_chat_member.return_value = SimpleNamespace(status="member")
        bot.create_chat_invite_link.side_effect = requests.Timeout("unknown result")
        with patch.object(campaign, "_utcnow", return_value=self.start), patch.object(campaign.time, "sleep"):
            with self.assertRaises(ValueError):
                campaign.create_link(bot, 101)
            with self.assertRaises(ValueError):
                campaign.create_link(bot, 101)
        bot.create_chat_invite_link.assert_called_once()
        self.assertEqual(self.sql("SELECT status FROM coupon_invite_links WHERE inviter_id=101"),
                         [{"status": "uncertain"}])

    def test_participant_must_join_before_receiving_new_or_existing_link(self):
        bot = Mock()
        bot.get_chat_member.return_value = SimpleNamespace(status="left")
        with patch.object(campaign, "_utcnow", return_value=self.start):
            with self.assertRaisesRegex(ValueError, "先加入频道"):
                campaign.create_link(bot, 100)
        bot.create_chat_invite_link.assert_not_called()
        self.assertEqual(self.sql("SELECT status FROM coupon_invite_links WHERE inviter_id=100"),
                         [{"status": "ready"}])
        self.assertNotIn("invite_link", campaign.mine(100)["link"])
        self.sql("UPDATE coupon_campaign_state SET request_after=NULL")
        with patch.object(campaign, "_utcnow", return_value=self.start):
            with self.assertRaisesRegex(ValueError, "先加入频道"):
                campaign.create_link(bot, 101)
        self.assertEqual(self.sql("SELECT status FROM coupon_invite_links WHERE inviter_id=101"),
                         [{"status": "failed"}])

    def test_joined_participant_gets_link_after_membership_check(self):
        bot = Mock()
        bot.get_chat_member.return_value = SimpleNamespace(status="member")
        bot.create_chat_invite_link.return_value = SimpleNamespace(invite_link="https://t.me/+new")
        with patch.object(campaign, "_utcnow", return_value=self.start), \
                patch.object(campaign.time, "sleep") as pause:
            self.assertEqual(campaign.create_link(bot, 101), "https://t.me/+new")
        bot.get_chat_member.assert_called_once_with(campaign.CHANNEL_ID, 101)
        pause.assert_called_once_with(2)
        bot.create_chat_invite_link.assert_called_once()

    def test_existing_link_is_returned_only_after_rechecking_membership(self):
        bot = Mock()
        bot.get_chat_member.return_value = SimpleNamespace(status="restricted", is_member=True)
        with patch.object(campaign, "_utcnow", return_value=self.start):
            self.assertEqual(campaign.create_link(bot, 100), "https://t.me/+test")
        bot.get_chat_member.assert_called_once_with(campaign.CHANNEL_ID, 100)
        bot.create_chat_invite_link.assert_not_called()
        self.assertEqual(self.sql("SELECT request_after FROM coupon_campaign_state")[0]["request_after"],
                         self.start + timedelta(seconds=2))

    def test_participant_verification_rate_limit_blocks_link_creation(self):
        bot = Mock()
        bot.get_chat_member.side_effect = apihelper.ApiTelegramException(
            "getChatMember", None,
            {"error_code": 429, "description": "limited", "parameters": {"retry_after": 60}})
        with patch.object(campaign, "_utcnow", return_value=self.start), self.assertLogs(campaign.logger):
            with self.assertRaisesRegex(ValueError, "无法核实"):
                campaign.create_link(bot, 101)
            with self.assertRaisesRegex(ValueError, "请求暂受限制"):
                campaign.create_link(bot, 102)
        bot.get_chat_member.assert_called_once()
        bot.create_chat_invite_link.assert_not_called()
        self.assertEqual(self.sql("SELECT request_after FROM coupon_campaign_state")[0]["request_after"],
                         self.start + timedelta(seconds=61))

    def test_membership_display_checks_status_and_shares_rate_limit(self):
        bot = Mock()
        bot.get_chat_member.return_value = SimpleNamespace(status="restricted", is_member=True)
        with patch.object(campaign, "_utcnow", return_value=self.start):
            self.assertTrue(campaign.participant_status(bot, 100))
            with self.assertRaisesRegex(ValueError, "受限"):
                campaign.participant_status(bot, 100)
        bot.get_chat_member.assert_called_once_with(campaign.CHANNEL_ID, 100)
        self.sql("UPDATE coupon_campaign_state SET request_after=NULL")
        bot.get_chat_member.side_effect = apihelper.ApiTelegramException(
            "getChatMember", None,
            {"error_code": 429, "description": "limited", "parameters": {"retry_after": 60}})
        with patch.object(campaign, "_utcnow", return_value=self.start), self.assertLogs(campaign.logger):
            with self.assertRaisesRegex(ValueError, "无法核实"):
                campaign.participant_status(bot, 100)
            with self.assertRaisesRegex(ValueError, "请求暂受限制"):
                campaign.create_link(bot, 100)
        self.assertEqual(self.sql("SELECT request_after FROM coupon_campaign_state")[0]["request_after"],
                         self.start + timedelta(seconds=61))

    def test_membership_display_does_not_guess_on_error_and_handles_nonmember(self):
        bot = Mock()
        bot.get_chat_member.return_value = SimpleNamespace(status="left")
        with patch.object(campaign, "_utcnow", return_value=self.start):
            self.assertFalse(campaign.participant_status(bot, 100))
        self.sql("UPDATE coupon_campaign_state SET request_after=NULL")
        bot.get_chat_member.side_effect = requests.Timeout("unavailable")
        with patch.object(campaign, "_utcnow", return_value=self.start), self.assertLogs(campaign.logger):
            with self.assertRaisesRegex(ValueError, "无法核实"):
                campaign.participant_status(bot, 100)
        self.assertEqual(self.sql("SELECT request_after FROM coupon_campaign_state")[0]["request_after"],
                         self.start + timedelta(minutes=1))

    def test_lost_bot_permission_suspends_campaign_and_preserves_existing_link(self):
        bot = Mock()
        bot.get_chat_member.side_effect = apihelper.ApiTelegramException(
            "getChatMember", None, {"error_code": 403, "description": "forbidden"})
        with patch.object(campaign, "_utcnow", return_value=self.start), self.assertLogs(campaign.logger):
            with self.assertRaisesRegex(ValueError, "无法核实"):
                campaign.create_link(bot, 100)
        self.assertFalse(campaign.is_enabled())
        self.assertEqual(self.sql("SELECT status FROM coupon_invite_links WHERE inviter_id=100"),
                         [{"status": "ready"}])
        bot.create_chat_invite_link.assert_not_called()

    def test_limit_pauses_all_membership_checks(self):
        joined = self.start + timedelta(hours=1)
        campaign.record_updates([update(1, 42, "left", "member", joined, "https://t.me/+test")], joined)
        due = joined + timedelta(days=7)
        self.sql("UPDATE coupon_campaign_state SET last_poll=%s", (due,))
        bot = Mock()
        bot.get_chat_member.side_effect = apihelper.ApiTelegramException(
            "getChatMember", None,
            {"error_code": 429, "description": "limited", "parameters": {"retry_after": 60}})
        with patch.object(campaign, "_utcnow", return_value=due):
            with self.assertLogs(campaign.logger, level="WARNING"):
                self.assertTrue(campaign.verify_one(bot))
            self.assertFalse(campaign.verify_one(bot))
        bot.get_chat_member.assert_called_once()
        self.assertEqual(self.sql("SELECT state FROM coupon_invites"), [{"state": "pending"}])
        self.assertEqual(self.sql("SELECT request_after FROM coupon_campaign_state")[0]["request_after"],
                         due + timedelta(seconds=61))

    def test_leave_after_seven_days_preserves_earned_eligibility(self):
        joined = self.start + timedelta(hours=1)
        left = joined + timedelta(days=7)
        campaign.record_updates([update(1, 42, "left", "member", joined, "https://t.me/+test")], joined)
        self.sql("UPDATE coupon_campaign_state SET last_poll=%s", (left,))
        campaign.record_updates([update(2, 42, "member", "left", left)], left)
        self.assertEqual(self.sql("SELECT state,left_at FROM coupon_invites"),
                         [{"state": "qualified", "left_at": left}])
        self.sql("UPDATE coupon_notifications SET status='sent' WHERE event_key='join_1'")
        bot = Mock()
        with patch.object(campaign, "_utcnow", return_value=left):
            self.assertTrue(campaign.notify_one(bot))
        self.assertIn("已满足连续留存7天，资格保留", bot.send_message.call_args.args[1])

    def test_late_exit_invalidates_unused_voucher_and_releases_other_four(self):
        joined = self.start + timedelta(hours=1)
        events = [update(index, index + 10, "left", "member", joined, "https://t.me/+test")
                  for index in range(1, 6)]
        campaign.record_updates(events, joined)
        due = joined + timedelta(days=7)
        self.sql("UPDATE coupon_campaign_state SET last_poll=%s", (due,))
        self.sql("UPDATE coupon_invites SET state='qualified'")
        voucher = campaign.issue_ready(due)
        campaign.record_updates([update(6, 11, "member", "left", joined + timedelta(days=6))], due)
        self.assertEqual(self.sql("SELECT status FROM coupon_vouchers WHERE id=%s", (voucher,)),
                         [{"status": "invalid"}])
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM coupon_invites WHERE state='qualified' AND voucher_id IS NULL")[0]["n"], 4)

    def test_notifies_inviter_of_guest_exit_once(self):
        joined = self.start + timedelta(hours=1)
        left = joined + timedelta(days=1)
        campaign.record_updates([update(1, 42, "left", "member", joined, "https://t.me/+test")], joined)
        self.sql("UPDATE coupon_campaign_state SET last_poll=%s", (left,))
        campaign.record_updates([update(2, 42, "member", "left", left)], left)
        self.sql("UPDATE coupon_notifications SET status='sent' WHERE event_key='join_1'")
        bot = Mock()
        with patch.object(campaign, "_utcnow", return_value=left):
            self.assertTrue(campaign.notify_one(bot))
            self.assertFalse(campaign.notify_one(bot))
        args = bot.send_message.call_args.args
        self.assertEqual(args[0], 100)
        self.assertIn("Guest 42", args[1])
        self.assertIn("2026-01-02 08:00", args[1])
        self.assertIn("未满足连续留存7天", args[1])
        bot.send_message.assert_called_once()

    def test_redeem_validates_customer_source_and_actual_amount_and_is_idempotent(self):
        now = datetime(2026, 1, 10)
        voucher, record = "a" * 32, "b" * 32
        self.sql("INSERT INTO coupon_vouchers(id,owner_id,issued_at,expires_at) VALUES(%s,100,%s,%s)",
                 (voucher, now, now + timedelta(days=30)))
        self.sql("INSERT INTO crm_customers VALUES(-1,100)")
        self.sql("INSERT INTO crm_consumptions VALUES(%s,-1,2,'venue',30,0,'valid',%s,'Service')",
                 (record, now + timedelta(days=1)))
        with patch.object(campaign, "_utcnow", return_value=now + timedelta(days=2)):
            with self.assertRaises(ValueError):
                campaign.redeem(100, voucher, record, "40", 9000, True, "c" * 32)
            self.assertEqual(campaign.redeem(100, voucher, record, "50", 9000, True, "c" * 32),
                             {"ok": True, "replayed": False})
            self.assertEqual(campaign.redeem(100, voucher, record, "50", 9000, True, "c" * 32),
                             {"ok": True, "replayed": True})
            with self.assertRaises(RecordConflict):
                campaign.redeem(100, voucher, record, "50", 9001, True, "c" * 32)
        self.assertEqual(self.sql("SELECT original_amount,status FROM coupon_vouchers"),
                         [{"original_amount": Decimal("50.00"), "status": "redeemed"}])

    def test_redeem_refuses_massage_and_under_twenty(self):
        now = datetime(2026, 1, 10)
        voucher, record = "a" * 32, "b" * 32
        self.sql("INSERT INTO coupon_vouchers(id,owner_id,issued_at,expires_at) VALUES(%s,100,%s,%s)",
                 (voucher, now, now + timedelta(days=30)))
        self.sql("INSERT INTO crm_customers VALUES(-1,100)")
        self.sql("INSERT INTO crm_consumptions VALUES(%s,-1,1,'venue',30,0,'valid',%s,'Service')",
                 (record, now + timedelta(days=1)))
        with patch.object(campaign, "_utcnow", return_value=now + timedelta(days=2)):
            with self.assertRaises(ValueError):
                campaign.redeem(100, voucher, record, "19", 9000, True, "c" * 32)
            with self.assertRaises(ValueError):
                campaign.redeem(100, voucher, record, "50", 9000, True, "c" * 32)
        self.assertEqual(self.sql("SELECT status FROM coupon_vouchers"), [{"status": "available"}])

    def test_bot_confirmation_is_bound_to_admin_and_cannot_redeem_again_after_reversal(self):
        now = datetime(2026, 1, 10)
        voucher, record = "a" * 32, "b" * 32
        self.sql("INSERT INTO coupon_vouchers(id,owner_id,issued_at,expires_at) VALUES(%s,100,%s,%s)",
                 (voucher, now, now + timedelta(days=30)))
        self.sql("INSERT INTO crm_customers VALUES(-1,100)")
        self.sql("INSERT INTO venues VALUES(2,'Venue')")
        self.sql("INSERT INTO crm_consumptions VALUES(%s,-1,2,'venue',30,0,'valid',%s,'Service')",
                 (record, now + timedelta(days=1)))
        with patch.object(campaign, "_utcnow", return_value=now + timedelta(days=2)):
            request = campaign.prepare_redemption(100, voucher, record, "50", 9000)
            self.assertEqual(request["venue"], "Venue")
            with self.assertRaises(ValueError):
                campaign.confirm_redemption(request["id"], 9001)
            self.assertEqual(campaign.confirm_redemption(request["id"], 9000),
                             {"ok": True, "replayed": False})
            campaign.reverse_redemption(voucher, "Correcting record", 9000, True, "d" * 32)
            self.assertEqual(campaign.confirm_redemption(request["id"], 9000),
                             {"ok": True, "replayed": True})
        self.assertEqual(self.sql("SELECT status FROM coupon_vouchers"), [{"status": "reversal_pending"}])
        self.assertEqual(self.sql("SELECT action FROM coupon_redemption_audit ORDER BY id"),
                         [{"action": "redeem"}, {"action": "reverse"}])


class CouponAccessTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-token", "ADMIN_USER_IDS": "123"})
        env.start()
        self.addCleanup(env.stop)
        self.client = api_server.app.test_client()

    def test_customer_can_only_query_their_verified_identity(self):
        with patch.object(campaign, "mine", return_value={"vouchers": []}) as mine:
            response = self.client.post("/webhook", json={
                "action": "coupon_mine", "init_data": signed(456), "owner_id": 999,
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        mine.assert_called_once_with(456, 1, 1)

    def test_bot_displays_join_invite_and_status_steps(self):
        bot = Mock()
        data = {"enabled": True, "eligible": 1, "invite_total": 1, "voucher_total": 0,
                "invites": [{"display_name": "Guest", "state": "pending", "left_at": None}],
                "vouchers": []}
        with patch.object(campaign, "mine", return_value=data), \
                patch.object(campaign, "participant_status", return_value=True):
            coupon_bot.show(bot, 456, 456)
        text = bot.send_message.call_args.args[1]
        self.assertIn("先加入频道", text)
        self.assertIn("已加入频道", text)
        self.assertIn("1 / 5", text)
        self.assertIn("Guest · 留存中", text)
        buttons = [button for row in bot.send_message.call_args.kwargs["reply_markup"].keyboard for button in row]
        self.assertTrue(any(button.url == "https://t.me/haotianyl99988866" for button in buttons))
        self.assertTrue(any(button.callback_data == "coupon:link" for button in buttons))
        self.assertTrue(any(button.callback_data == "coupon:mine" for button in buttons))
        self.assertTrue(any(button.callback_data == "coupon:vouchers" for button in buttons))

    def test_bot_displays_unknown_membership_and_independent_voucher_page(self):
        bot = Mock()
        data = {"enabled": False, "eligible": 0, "invite_total": 0, "voucher_total": 0,
                "invites": [], "vouchers": []}
        with patch.object(campaign, "mine", return_value=data), \
                patch.object(campaign, "participant_status", side_effect=ValueError("暂时无法核实")):
            coupon_bot.show(bot, 456, 456)
            self.assertIn("暂时无法核实", bot.send_message.call_args.args[1])
            call = SimpleNamespace(id="callback", from_user=SimpleNamespace(id=456),
                                   message=SimpleNamespace(chat=SimpleNamespace(id=456, type="private")),
                                   data="coupon:vouchers")
            coupon_bot.callback(bot, call)
        self.assertIn("暂无优惠券", bot.send_message.call_args.args[1])
        self.assertEqual(bot.send_message.call_args.args[0], 456)

    def test_bot_voucher_page_shows_expired_and_redeemed_status(self):
        bot = Mock()
        data = {"voucher_total": 2, "vouchers": [
            {"id": "a", "status": "available", "expires_at": "2026-01-01T00:00:00Z"},
            {"id": "b", "status": "redeemed", "expires_at": "2026-02-01T00:00:00Z"},
        ]}
        with patch.object(campaign, "mine", return_value=data) as mine, \
                patch.object(campaign, "_utcnow", return_value=datetime(2026, 1, 2)):
            coupon_bot.show_vouchers(bot, 456, 456)
        mine.assert_called_once_with(456)
        text = bot.send_message.call_args.args[1]
        self.assertIn("已过期", text)
        self.assertIn("已核销", text)

    def test_bot_shares_existing_channel_link_with_instructions(self):
        bot = Mock()
        call = SimpleNamespace(id="callback", from_user=SimpleNamespace(id=456),
                               message=SimpleNamespace(chat=SimpleNamespace(id=456, type="private")),
                               data="coupon:link")
        with patch.object(campaign, "create_link", return_value="https://t.me/+example"):
            coupon_bot.callback(bot, call)
        text = bot.send_message.call_args.args[1]
        self.assertIn("https://t.me/+example", text)
        self.assertIn("留存7天", text)
        self.assertIn("分享文案", text)

    def test_nonadmin_and_tenant_cannot_redeem_or_preview(self):
        with patch.object(campaign, "redeem") as redeem, patch.object(campaign, "preview") as preview:
            for identity in (signed(456), signed(123, start_param="tenant_test-shop")):
                for action in ("admin_coupon_redeem", "admin_coupon_preview"):
                    response = self.client.post("/webhook", json={"action": action, "init_data": identity})
                    self.assertEqual(response.status_code, 403)
            redeem.assert_not_called()
            preview.assert_not_called()

    def test_bot_redeem_confirmation_requires_administrator_in_own_private_chat(self):
        bot = Mock()
        call = SimpleNamespace(
            id="callback", from_user=SimpleNamespace(id=456),
            message=SimpleNamespace(chat=SimpleNamespace(id=456, type="private")),
            data="coupon:confirm:" + "a" * 32,
        )
        with patch.object(campaign, "confirm_redemption") as confirm:
            coupon_bot.callback(bot, call)
        confirm.assert_not_called()
        self.assertTrue(bot.answer_callback_query.call_args.kwargs["show_alert"])


if __name__ == "__main__":
    unittest.main()
