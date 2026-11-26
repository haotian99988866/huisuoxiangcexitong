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
        self.assertEqual(campaign.expiry(datetime(2026, 1, 31, 12)), datetime(2026, 3, 31, 12))
        self.assertEqual(campaign.expiry(datetime(2026, 1, 31, 18)), datetime(2026, 3, 31, 18))
        self.assertEqual(campaign.expiry(datetime(2026, 12, 31, 12)), datetime(2027, 2, 28, 12))
        self.assertEqual(campaign.expiry(datetime(2023, 12, 31, 12)), datetime(2024, 2, 29, 12))
        self.assertEqual(campaign.expiry(datetime(2026, 12, 30, 18)), datetime(2027, 2, 27, 18))

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
        self.assertEqual(bot.send_message.call_args.kwargs["reply_markup"].keyboard[0][0].callback_data,
                         "coupon:mine")
        self.assertEqual(self.sql("SELECT state FROM coupon_invites"), [{"state": "pending"}])
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM coupon_vouchers")[0]["n"], 0)

    def test_due_verifications_do_not_starve_join_notifications(self):
        joined = self.start + timedelta(hours=1)
        campaign.record_updates([
            update(1, 42, "left", "member", joined, "https://t.me/+test"),
            update(2, 43, "left", "member", joined, "https://t.me/+test"),
        ], joined)
        due = joined + timedelta(days=7)
        self.sql("UPDATE coupon_campaign_state SET last_poll=%s", (due,))
        bot = Mock()
        bot.get_chat_member.return_value = SimpleNamespace(status="member")
        with patch.object(campaign, "_utcnow", return_value=due):
            campaign._work_cycle(bot, notify_first=False)
        bot.send_message.assert_not_called()
        with patch.object(campaign, "_utcnow", return_value=due + timedelta(seconds=10)):
            campaign._work_cycle(bot, notify_first=True)
        bot.send_message.assert_called_once()
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM coupon_invites WHERE state='qualified'")[0]["n"], 1)
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM coupon_invites WHERE state='pending'")[0]["n"], 1)

    def test_voucher_notification_opens_voucher_page(self):
        voucher = "a" * 32
        self.sql("INSERT INTO coupon_vouchers(id,owner_id,issued_at,expires_at) VALUES(%s,100,%s,%s)",
                 (voucher, self.start, self.start + timedelta(days=30)))
        self.sql("""INSERT INTO coupon_notifications(event_key,owner_id,voucher_id,created_at)
            VALUES(%s,100,%s,%s)""", (f"voucher_{voucher}", voucher, self.start))
        bot = Mock()
        with patch.object(campaign, "_utcnow", return_value=self.start):
            self.assertTrue(campaign.notify_one(bot))
        self.assertEqual(bot.send_message.call_args.kwargs["reply_markup"].keyboard[0][0].callback_data,
                         "coupon:vouchers")

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
        self.assertEqual(self.sql("SELECT expires_at FROM coupon_vouchers WHERE id=%s", (voucher,)),
                         [{"expires_at": datetime(2026, 3, 8, 1, 0, 24)}])
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

    def test_link_waits_for_short_membership_check_cooldown_without_skipping_recheck(self):
        bot = Mock()
        bot.get_chat_member.return_value = SimpleNamespace(status="member")
        clock = [self.start]

        def sleep(seconds):
            clock[0] += timedelta(seconds=seconds)

        with patch.object(campaign, "_utcnow", side_effect=lambda: clock[0]), \
                patch.object(campaign.time, "sleep", side_effect=sleep):
            self.assertTrue(campaign.participant_status(bot, 100))
            self.assertEqual(campaign.create_link(bot, 100), "https://t.me/+test")
        self.assertEqual(bot.get_chat_member.call_count, 2)
        bot.create_chat_invite_link.assert_not_called()

    def test_progress_uses_only_unallocated_qualified_and_earliest_pending(self):
        for invitee, state, joined, voucher in (
                (11, "qualified", self.start, None),
                (12, "qualified", self.start, None),
                (13, "qualified", self.start, "a" * 32),
                (14, "pending", self.start + timedelta(hours=4), None),
                (15, "pending", self.start + timedelta(hours=1), None),
                (16, "pending", self.start + timedelta(hours=3), None),
                (17, "pending", self.start + timedelta(hours=2), None),
                (18, "left", self.start, None)):
            self.sql("""INSERT INTO coupon_invites
                (invitee_id,inviter_id,joined_at,state,voucher_id) VALUES(%s,100,%s,%s,%s)""",
                     (invitee, joined, state, voucher))
        data = campaign.mine(100)
        self.assertEqual(data["eligible"], 2)
        self.assertEqual(data["retaining"], 4)
        self.assertEqual(data["estimated_at"], "2026-01-08T03:00:00Z")
        self.assertEqual(data["invite_total"], 8)
        self.assertEqual(next(row for row in data["invites"] if row["invitee_id"] == 13)["voucher_id"], "a" * 32)
        self.sql("UPDATE coupon_invites SET state='left' WHERE invitee_id IN (14,15)")
        self.assertIsNone(campaign.mine(100)["estimated_at"])

    def test_existing_participant_start_renders_real_database_progress(self):
        self.sql("""INSERT INTO coupon_invites
            (invitee_id,inviter_id,display_name,joined_at,state)
            VALUES(42,100,'Guest',%s,'pending')""", (self.start,))
        bot = Mock()
        with patch.object(coupon_bot, "send_tracked") as send, \
                patch.object(campaign, "participant_status") as member:
            self.assertTrue(coupon_bot.show_start(bot, 100, 100))
        text = send.call_args.args[2]
        self.assertIn("Guest · 留存中", text)
        self.assertIn("还需邀请：4人", text)
        self.assertIn("还需达标：5人", text)
        member.assert_not_called()

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

    def test_new_participant_gets_join_or_link_based_on_membership(self):
        bot = Mock()
        data = {"enabled": True, "link": None, "eligible": 0, "retaining": 0,
                "estimated_at": None, "invite_total": 0, "voucher_total": 0,
                "invites": [], "vouchers": []}
        with patch.object(campaign, "mine", return_value=data), \
                patch.object(campaign, "participant_status", return_value=True):
            coupon_bot.show(bot, 456, 456)
        text = bot.send_message.call_args.args[1]
        self.assertIn("已加入频道", text)
        self.assertIn("【活动规则】\n好友通过专属链接入频道，留存7天并通过核验", text)
        self.assertIn("有效期2个月", text)
        self.assertIn("每次限1张，不可叠加", text)
        self.assertIn("已达标：0人  ·  留存中：0人", text)
        self.assertIn("还需邀请：5人  ·  还需达标：5人", text)
        self.assertIn("好友状态以已记录的频道事件及核验为准。", text)
        self.assertIn("目前尚无通过专属链接加入的好友。", text)
        self.assertLess(len(text), 300)
        rows = bot.send_message.call_args.kwargs["reply_markup"].keyboard
        self.assertEqual([len(row) for row in rows], [2, 2])
        buttons = [button for row in rows for button in row]
        self.assertTrue(any(button.callback_data == "coupon:link" for button in buttons))
        self.assertTrue(any(button.callback_data == "coupon:mine" for button in buttons))
        self.assertTrue(any(button.callback_data == "coupon:vouchers" for button in buttons))
        self.assertEqual((rows[1][1].text, rows[1][1].url),
                         ("联系昊天", "https://t.me/haotian99988866"))
        with patch.object(campaign, "mine", return_value=data), \
                patch.object(campaign, "participant_status", return_value=False):
            coupon_bot.show(bot, 456, 456)
        self.assertIn("请先加入频道", bot.send_message.call_args.args[1])
        rows = bot.send_message.call_args.kwargs["reply_markup"].keyboard
        self.assertEqual([len(row) for row in rows], [2, 2])
        buttons = [button for row in rows for button in row]
        self.assertTrue(any(button.url == "https://t.me/haotianyl99988866" for button in buttons))
        self.assertFalse(any(button.callback_data == "coupon:link" for button in buttons))
        self.assertTrue(any(button.text == "我已加入，继续" and button.callback_data == "coupon:mine"
                            for button in buttons))
        self.assertEqual((rows[1][1].text, rows[1][1].url),
                         ("联系昊天", "https://t.me/haotian99988866"))

    def test_join_continue_rechecks_membership_before_showing_link(self):
        bot = Mock()
        call = SimpleNamespace(id="callback", from_user=SimpleNamespace(id=456),
                               message=SimpleNamespace(chat=SimpleNamespace(id=456, type="private")),
                               data="coupon:mine")
        data = {"enabled": True, "link": None, "eligible": 0, "retaining": 0, "estimated_at": None,
                "invite_total": 0, "voucher_total": 0, "invites": [], "vouchers": []}
        with patch.object(campaign, "mine", return_value=data), \
                patch.object(campaign, "participant_status", return_value=True) as member, \
                patch.object(campaign, "create_link") as create:
            coupon_bot.callback(bot, call)
        member.assert_called_once_with(bot, 456)
        create.assert_not_called()
        buttons = [button for row in bot.send_message.call_args.kwargs["reply_markup"].keyboard for button in row]
        self.assertTrue(any(button.callback_data == "coupon:link" for button in buttons))

    def test_bot_displays_unknown_membership_and_independent_voucher_page(self):
        bot = Mock()
        data = {"enabled": True, "link": None, "eligible": 0, "retaining": 0, "estimated_at": None,
                "invite_total": 0, "voucher_total": 0,
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

    def test_participant_sees_progress_and_invite_pages_without_member_lookup(self):
        bot = Mock()
        invites = [{"display_name": f"Guest {i}", "username": "", "state": "pending",
                    "joined_at": "2026-01-01T00:00:00Z", "left_at": None, "voucher_id": None}
                   for i in range(7)]
        data = {"enabled": True, "link": {"status": "ready"}, "eligible": 2, "retaining": 3,
                "estimated_at": "2026-01-08T00:00:00Z", "invite_total": 7, "voucher_total": 0,
                "invites": invites, "vouchers": []}
        with patch.object(campaign, "mine", return_value=data), \
                patch.object(campaign, "participant_status") as member, \
                patch.object(campaign, "_utcnow", return_value=datetime(2026, 1, 7)):
            coupon_bot.show(bot, 456, 456)
            text = bot.send_message.call_args.args[1]
            self.assertIn("已达标：2人  ·  留存中：3人", text)
            self.assertIn("还需邀请：0人  ·  还需达标：3人", text)
            self.assertIn("留存好友退出或未通过核验，可能需再邀请", text)
            self.assertIn("预计还需 1天0小时0分", text)
            self.assertIn("【邀请记录】", text)
            self.assertIn("Guest 0 · 留存中", text)
            self.assertNotIn("Guest 5", text)
            buttons = [button.callback_data for row in bot.send_message.call_args.kwargs["reply_markup"].keyboard for button in row]
            self.assertIn("coupon:page:2", buttons)
            rows = bot.send_message.call_args.kwargs["reply_markup"].keyboard
            self.assertEqual([len(row) for row in rows], [2, 2, 1])
            self.assertEqual((rows[1][1].text, rows[1][1].url),
                             ("联系昊天", "https://t.me/haotian99988866"))
            coupon_bot.show(bot, 456, 456, page=2)
            self.assertIn("Guest 5 · 留存中", bot.send_message.call_args.args[1])
            member.assert_not_called()

    def test_participant_statuses_distinguish_consumed_left_and_review(self):
        data = {"enabled": True, "link": {"status": "ready"}, "eligible": 0, "retaining": 0,
                "estimated_at": None, "invite_total": 3, "voucher_total": 1, "vouchers": [],
                "invites": [
                    {"display_name": "Used", "username": "", "state": "qualified",
                     "joined_at": "2026-01-01T00:00:00Z", "left_at": None, "voucher_id": "a" * 32},
                    {"display_name": "Left", "username": "", "state": "left",
                     "joined_at": "2026-01-01T00:00:00Z", "left_at": "2026-01-02T00:00:00Z", "voucher_id": None},
                    {"display_name": "Review", "username": "", "state": "review",
                     "joined_at": "2026-01-01T00:00:00Z", "left_at": None, "voucher_id": None},
                ]}
        bot = Mock()
        with patch.object(campaign, "mine", return_value=data):
            coupon_bot.show(bot, 456, 456)
        text = bot.send_message.call_args.args[1]
        self.assertIn("Used · 已计入代金券", text)
        self.assertIn("Left · 未满7天退出", text)
        self.assertIn("Review · 待人工核实", text)

    def test_invite_pagination_crosses_database_page_boundary(self):
        data = {"enabled": True, "link": {"status": "ready"}, "eligible": 0, "retaining": 0,
                "estimated_at": None, "invite_total": 21, "voucher_total": 0, "vouchers": [],
                "invites": [{"display_name": "Guest 21", "username": "", "state": "left",
                             "joined_at": "2026-01-01T00:00:00Z", "left_at": None, "voucher_id": None}]}
        bot = Mock()
        call = SimpleNamespace(id="callback", from_user=SimpleNamespace(id=456),
                               message=SimpleNamespace(chat=SimpleNamespace(id=456, type="private")),
                               data="coupon:page:5")
        with patch.object(campaign, "mine", return_value=data) as mine:
            coupon_bot.callback(bot, call)
        mine.assert_called_once_with(456, 2)
        self.assertIn("Guest 21 · 未满7天退出", bot.send_message.call_args.args[1])
        buttons = [button.callback_data for row in bot.send_message.call_args.kwargs["reply_markup"].keyboard for button in row]
        self.assertIn("coupon:page:4", buttons)
        self.assertNotIn("coupon:page:6", buttons)

    def test_start_dispatches_to_campaign_or_existing_welcome(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123456:test-token"}):
            import main
        message = SimpleNamespace(text="/start", chat=SimpleNamespace(id=456),
                                  from_user=SimpleNamespace(id=456, to_dict=lambda: {"id": 456}))
        with patch("customer_analytics.bot_event"), patch.object(main, "touch_user"), \
                patch.object(main.menu_messages, "delete_command"), \
                patch.object(main, "send_welcome") as welcome, \
                patch.object(coupon_bot, "show_start", return_value=True) as start:
            main.handle_start(message)
            start.assert_called_once_with(main.bot, 456, 456)
            welcome.assert_not_called()
            start.return_value = False
            main.handle_start(message)
            welcome.assert_called_once_with(main.bot, 456, 456)

    def test_callback_reports_database_failure_after_answering(self):
        bot = Mock()
        call = SimpleNamespace(id="callback", from_user=SimpleNamespace(id=456),
                               message=SimpleNamespace(chat=SimpleNamespace(id=456, type="private")),
                               data="coupon:mine")
        with patch.object(campaign, "mine", side_effect=pymysql.MySQLError("offline")), \
                self.assertLogs(coupon_bot.logger, level="ERROR"):
            coupon_bot.callback(bot, call)
        bot.answer_callback_query.assert_called_once_with("callback")
        self.assertIn("暂不可用", bot.send_message.call_args.args[1])

    def test_progress_never_guesses_when_invites_are_insufficient_or_verification_is_due(self):
        data = {"enabled": True, "eligible": 2, "retaining": 1, "estimated_at": None}
        self.assertIn("还需邀请：2人", "\n".join(coupon_bot._progress(data)))
        self.assertIn("完成时间待定", "\n".join(coupon_bot._progress(data)))
        data.update(retaining=3, estimated_at="2026-01-08T00:00:00Z")
        with patch.object(campaign, "_utcnow", return_value=datetime(2026, 1, 8)):
            self.assertIn("等待系统核验", "\n".join(coupon_bot._progress(data)))

    def test_disabled_start_keeps_development_message_for_new_user_but_existing_progress_is_available(self):
        data = {"enabled": False, "link": None, "eligible": 0, "retaining": 0,
                "estimated_at": None, "invite_total": 0, "voucher_total": 0,
                "invites": [], "vouchers": []}
        bot = Mock()
        with patch.object(campaign, "mine", return_value=data), \
                patch.object(coupon_bot, "send_tracked") as send, \
                patch.object(campaign, "participant_status") as member:
            self.assertFalse(coupon_bot.show_start(bot, 456, 456))
            data["link"] = {"status": "ready"}
            self.assertTrue(coupon_bot.show_start(bot, 456, 456))
            self.assertIn("活动暂未开放", send.call_args.args[2])
            self.assertEqual([len(row) for row in send.call_args.kwargs["reply_markup"].keyboard], [2, 1])
            member.assert_not_called()

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
        self.assertEqual([len(row) for row in bot.send_message.call_args.kwargs["reply_markup"].keyboard], [2])
        buttons = [button for row in bot.send_message.call_args.kwargs["reply_markup"].keyboard for button in row]
        self.assertTrue(any(button.callback_data == "coupon:mine" for button in buttons))
        self.assertTrue(any(button.url and button.url.startswith("https://t.me/share/url?") for button in buttons))

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
