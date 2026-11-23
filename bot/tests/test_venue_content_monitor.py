import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

import venue_content_monitor as monitor


def message(kind="text", text="", caption="", photo=None, channel_id=None):
    return SimpleNamespace(
        chat=SimpleNamespace(id=monitor.CHANNEL_ID if channel_id is None else channel_id), message_id=123,
        content_type=kind, text=text, caption=caption, photo=photo,
    )


class DalangContentMonitorTests(unittest.TestCase):
    def test_risk_checks_all_age_mentions_and_sensitive_terms(self):
        risks, has_age = monitor.inspect_text("年龄: 23\n16岁\n博彩")
        self.assertTrue(has_age)
        self.assertIn("疑似未成年人信息", risks)
        self.assertIn("疑似赌博相关信息", risks)
        self.assertIn("疑似毒品相关信息", monitor.inspect_text("毒品")[0])
        self.assertEqual(monitor.inspect_text("工号 1618，年龄 23岁")[0], [])
        self.assertIn("疑似未成年人信息", monitor.inspect_text("未满18岁")[0])
        self.assertIn("疑似未成年人信息", monitor.inspect_text("十八岁以下")[0])

    def test_photo_risk_stops_ingestion_and_deletes_message(self):
        bot = MagicMock()
        bot.get_file.return_value.file_path = "image"
        with patch.object(monitor, "ocr_image_text", return_value="年龄 16"), \
                patch.object(monitor, "_quarantine_indexed", return_value=True), \
                patch.object(monitor, "_notify") as notify:
            stopped = monitor.handle(bot, message("photo", photo=[SimpleNamespace(file_id="file")]))
        self.assertTrue(stopped)
        bot.delete_message.assert_called_once_with(monitor.CHANNEL_ID, 123)
        self.assertIn("已删除", notify.call_args.args[3])

    def test_unknown_photo_and_video_are_retained_and_alerted(self):
        bot = MagicMock()
        bot.get_file.return_value.file_path = "image"
        with patch.object(monitor, "ocr_image_text", return_value="价格表"), \
                patch.object(monitor, "_notify") as notify:
            self.assertFalse(monitor.handle(bot, message("photo", photo=[SimpleNamespace(file_id="file")])))
            self.assertFalse(monitor.handle(bot, message("video")))
        self.assertEqual(notify.call_count, 2)
        bot.delete_message.assert_not_called()

    def test_photo_download_failure_alerts_without_deleting(self):
        bot = MagicMock()
        bot.get_file.side_effect = requests.Timeout()
        with patch.object(monitor, "_notify") as notify:
            self.assertFalse(monitor.handle(bot, message("photo", photo=[SimpleNamespace(file_id="file")])))
        self.assertIn("识别失败", notify.call_args.args[2][0])
        bot.delete_message.assert_not_called()

    def test_failed_delete_and_quarantine_are_explicit_and_stop_ingestion(self):
        bot = MagicMock()
        bot.delete_message.side_effect = requests.Timeout()
        with patch.object(monitor, "_quarantine_indexed", return_value=False), \
                patch.object(monitor, "_notify") as notify:
            self.assertTrue(monitor.handle(bot, message(text="年龄 17")))
        self.assertIn("删除失败", notify.call_args.args[3])
        self.assertIn("隔离未确认", notify.call_args.args[3])

    def test_quarantine_blocks_existing_person_and_prevents_source_resync(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        with patch.object(monitor, "get_conn", return_value=conn):
            self.assertTrue(monitor._quarantine_indexed(message("photo")))
        queries = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertIn("m.blocked=1", queries[0])
        self.assertIn("file_id=''", queries[1])
        conn.commit.assert_called_once()

    def test_quarantine_uses_the_flagged_venues_id_for_every_lookup(self):
        from config.channels import STAFF_ARCHIVE_VENUES
        for venue_id in (5, 6):
            conn = MagicMock()
            cursor = conn.cursor.return_value.__enter__.return_value
            post = message("photo", channel_id=STAFF_ARCHIVE_VENUES[venue_id]["channel_id"])
            with self.subTest(venue_id=venue_id), patch.object(monitor, "get_conn", return_value=conn):
                self.assertTrue(monitor._quarantine_indexed(post, venue_id))
            args = cursor.execute.call_args_list[0].args[1]
            self.assertEqual(args[0], venue_id)
            self.assertEqual(args[3], venue_id)
            self.assertEqual(args[6], venue_id)
            self.assertEqual(cursor.execute.call_args_list[1].args[1][0], venue_id)

    def test_unconfigured_channels_do_not_match(self):
        other = message(text="年龄 16")
        other.chat.id = -1001
        self.assertFalse(monitor.matches(other))

    def test_fivexiang_and_dihao_channels_use_same_monitor_and_quarantine_scope(self):
        from config.channels import STAFF_ARCHIVE_VENUES
        for venue_id in (5, 6):
            with self.subTest(venue_id=venue_id):
                post = message(text="未满18岁", channel_id=STAFF_ARCHIVE_VENUES[venue_id]["channel_id"])
                bot = MagicMock()
                with patch.object(monitor, "_quarantine_indexed", return_value=True) as quarantine, \
                        patch.object(monitor, "_notify") as notify:
                    self.assertTrue(monitor.matches(post))
                    self.assertTrue(monitor.handle(bot, post))
                quarantine.assert_called_once_with(post, venue_id)
                bot.delete_message.assert_called_once_with(post.chat.id, post.message_id)
                self.assertEqual(notify.call_args.args[-1], venue_id)

    def test_archive_reposts_do_not_trigger_duplicate_uncertain_media_alerts(self):
        from config.channels import STAFF_ARCHIVE_VENUES
        for venue_id, config in STAFF_ARCHIVE_VENUES.items():
            post = message("photo", caption=config["repost_prefix"] + "工号：123",
                           photo=[SimpleNamespace(file_id="file")], channel_id=config["channel_id"])
            bot = MagicMock()
            with self.subTest(venue_id=venue_id), patch.object(monitor, "_notify") as notify:
                self.assertFalse(monitor.handle(bot, post))
                bot.get_file.assert_not_called()
                notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
