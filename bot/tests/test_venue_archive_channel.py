import re
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from telebot import apihelper

import venue_archive_channel as channel
from config.channels import STAFF_ARCHIVE_VENUES


def member(ident, *, group="1牌", price="199", cover=None):
    return {
        "id": ident, "code": str(ident), "group_name": group,
        "price_usd": price, "price_detail": f"{group} · 90分钟" if price else "价格待确认",
        "cover": cover if cover is not None else ident,
    }


class DalangChannelTests(unittest.TestCase):
    @patch.object(channel, "schedule_footer")
    def test_dalang_wuxiang_and_dihao_archive_updates_schedule_footer_refresh(
        self, schedule_footer
    ):
        bot = Mock()
        for venue_id in (4, 5, 6):
            channel._schedule_footer_after_update(bot, venue_id, True)
            self.assertEqual(
                schedule_footer.call_args.args,
                (bot, STAFF_ARCHIVE_VENUES[venue_id]["channel_id"]),
            )
        channel._schedule_footer_after_update(bot, 3, True)
        channel._schedule_footer_after_update(bot, 6, False)
        self.assertEqual(schedule_footer.call_count, 3)

    def test_navigation_paginates_and_links_only_published_current_staff(self):
        members = [member(ident) for ident in range(1, 126)]
        published = {ident: 20000 + ident for ident in range(1, 125)}
        published[999] = 20999
        pages = channel.navigation_pages(members, published)
        urls = [url for page in pages for url in re.findall(r'href="([^"]+)"', page)]
        self.assertEqual(len(pages), 2)
        self.assertEqual(len(urls), 124)
        self.assertEqual(len(set(urls)), 124)
        self.assertNotIn("https://t.me/dlts02/20999", urls)
        self.assertTrue(all(len(re.findall(r'href=', page)) + page.count("<b>") <= 100
                            for page in pages))
        self.assertTrue(all("共 124 人" in page for page in pages))
        self.assertTrue(all("点击工号查看频道档案" not in page for page in pages))

    def test_navigation_keeps_84_staff_with_ten_groups_on_one_page(self):
        members = [member(ident, group=f"{ident % 10}牌") for ident in range(1, 85)]
        pages = channel.navigation_pages(members, {ident: 20000 + ident for ident in range(1, 85)})
        self.assertEqual(len(pages), 1)
        self.assertEqual(len(re.findall(r'href=', pages[0])), 84)
        self.assertNotIn("1/2", pages[0])

    def test_navigation_splits_only_when_entity_limit_would_be_exceeded(self):
        members = [member(ident) for ident in range(1, 100)]
        published = {ident: 20000 + ident for ident in range(1, 100)}
        single = channel.navigation_pages(members[:98], published)
        self.assertEqual(len(single), 1)
        self.assertEqual(len(re.findall(r'href=', single[0])) + single[0].count("<b>"), 100)
        pages = channel.navigation_pages(members, published)
        self.assertEqual(len(pages), 2)
        self.assertEqual(sum(len(re.findall(r'href=', page)) for page in pages), 99)
        self.assertTrue(all(len(re.findall(r'href=', page)) + page.count("<b>") <= 100
                            for page in pages))

    def test_navigation_counts_non_bmp_characters_against_message_length(self):
        members = [member(1, group="🌀" * 800), member(2, group="🌟" * 800)]
        pages = channel.navigation_pages(members, {1: 201, 2: 202})
        self.assertEqual(len(pages), 2)
        self.assertTrue(all(len(re.findall(r'href=', page)) == 1 for page in pages))
        with self.assertRaisesRegex(ValueError, "大浪"):
            channel.navigation_pages([member(1, group="🌀" * 1600)], {1: 201})
        with self.assertRaisesRegex(ValueError, STAFF_ARCHIVE_VENUES[6]["venue_name"]):
            channel.navigation_pages([member(1, group="🌀" * 1600)], {1: 201}, venue_id=6)

    def test_navigation_shows_unknown_price_and_page_buttons(self):
        pages = channel.navigation_pages([member(1, group="类别待确认", price=None)], {1: 201})
        self.assertIn("价格待确认 · 时长待确认", pages[0])
        keyboard = channel.navigation_keyboard(1, 2, {1: 20045, 2: 20046}).to_dict()
        self.assertEqual(keyboard["inline_keyboard"][0][0]["url"], "https://t.me/dlts02/20046")
        self.assertEqual([len(row) for row in keyboard["inline_keyboard"]], [1, 2, 2])
        self.assertIn("价格：待确认\n时长：待确认", channel.caption(member(1, price=None)))

    def test_four_buttons_use_stable_staff_identity(self):
        keyboard = channel.photo_keyboard(member(88), "example_bot").to_dict()
        self.assertEqual([len(row) for row in keyboard["inline_keyboard"]], [2, 2])
        self.assertTrue(keyboard["inline_keyboard"][0][0]["url"].endswith("staff_4_88"))

    def test_each_venue_uses_its_own_channel_and_archive_context(self):
        for venue_id, config in STAFF_ARCHIVE_VENUES.items():
            with self.subTest(venue_id=venue_id):
                entry = member(88, group="类别")
                keyboard = channel.photo_keyboard(entry, "example_bot", venue_id).to_dict()
                self.assertIn(f"staff_{venue_id}_88", keyboard["inline_keyboard"][0][0]["url"])
                self.assertEqual(
                    keyboard["inline_keyboard"][0][1]["url"],
                    "https://t.me/haotianyl99988866",
                )
                pages = channel.navigation_pages([entry], {88: 201}, venue_id)
                self.assertIn(config["archive_title"], pages[0])
                self.assertIn(f"https://t.me/{config['channel_name']}/201", pages[0])
                self.assertTrue(pages[0].startswith(channel.FULL_ROSTER_MARKER))
                self.assertTrue(channel.caption(entry, venue_id).startswith(config["repost_prefix"]))
                nav_buttons = channel.navigation_keyboard(1, 1, {1: 201}, venue_id).to_dict()
                nav_url = next(
                    button["url"] for row in nav_buttons["inline_keyboard"] for button in row
                    if button["text"] == "西港会所导航"
                )
                self.assertEqual(nav_url, "https://t.me/haotianyl99988866")

    @patch.object(channel, "get_conn")
    def test_archive_tables_are_separate_for_each_venue(self, get_conn):
        for venue_id, config in STAFF_ARCHIVE_VENUES.items():
            channel.ensure_tables(venue_id)
        statements = [
            call.args[0]
            for call in get_conn.return_value.cursor.return_value.__enter__.return_value.execute.call_args_list
        ]
        for config in STAFF_ARCHIVE_VENUES.values():
            self.assertTrue(any(f"CREATE TABLE IF NOT EXISTS {config['table_prefix']}_policy" in sql
                                for sql in statements))

    def test_429_wait_is_persisted_and_item_remains_pending(self):
        cur, conn = Mock(), Mock()
        error = apihelper.ApiTelegramException(
            "sendPhoto", Mock(), {"error_code": 429, "description": "Too Many Requests",
                                  "parameters": {"retry_after": 25}},
        )
        send = Mock(side_effect=error)
        channel._request(cur, conn, "dalang_archive_posts", "staff_id", 9, send, lambda _: True)
        send.assert_called_once()
        calls = cur.execute.call_args_list
        self.assertTrue(any("next_request_at" in call.args[0] and call.args[1] == (26,)
                            for call in calls))
        self.assertEqual(calls[-1].args[1][0], "pending")
        self.assertEqual(conn.commit.call_count, 2)

    def test_unknown_send_outcome_is_not_requeued(self):
        cur, conn = Mock(), Mock()
        send = Mock(side_effect=TimeoutError())
        channel._request(cur, conn, "dalang_archive_posts", "staff_id", 9, send, lambda _: True)
        send.assert_called_once()
        self.assertEqual(cur.execute.call_args_list[-1].args[1][0], "uncertain")
        self.assertEqual(conn.commit.call_count, 2)

    def test_unknown_edit_outcome_requires_review(self):
        for error in (
            TimeoutError(),
            apihelper.ApiTelegramException(
                "editMessageText", Mock(), {"error_code": 500, "description": "Internal error"},
            ),
        ):
            with self.subTest(error=type(error).__name__):
                cur, conn = Mock(), Mock()
                channel._request(cur, conn, "dalang_archive_pages", "page_number", 1,
                                 Mock(side_effect=error), lambda _: True, editing=True)
                self.assertEqual(cur.execute.call_args_list[-1].args[1][0], "uncertain")

    @patch.object(channel, "photo")
    @patch.object(channel, "detail")
    def test_new_post_uses_real_eligible_cover_and_is_saved_once(self, detail, photo):
        entry = member(8)
        detail.return_value = {"venue_id": 4, "photos": [{"id": 8}]}
        photo.return_value = {"venue_id": 4, "media_type": "photo", "file_id": "file"}
        markup = channel.photo_keyboard(entry, "example_bot")
        bot = Mock()
        bot.send_photo.return_value = SimpleNamespace(
            chat=SimpleNamespace(id=channel.CHANNEL_ID), message_id=20055,
            photo=[object()], caption=channel.caption(entry), reply_markup=markup,
        )
        cur, conn = Mock(), Mock()
        self.assertTrue(channel._send_post(bot, "example_bot", cur, conn, entry))
        bot.send_photo.assert_called_once()
        self.assertEqual(cur.execute.call_args_list[-1].args[1], (20055, 8))
        self.assertEqual(conn.commit.call_count, 2)

    @patch.object(channel, "photo")
    @patch.object(channel, "detail")
    def test_new_venue_post_uses_that_venues_existing_public_profile(self, detail, photo):
        entry = member(8, group="贵妃")
        detail.return_value = {"venue_id": 6, "photos": [{"id": 8}]}
        photo.return_value = {"venue_id": 6, "media_type": "photo", "file_id": "file"}
        markup = channel.photo_keyboard(entry, "example_bot", 6)
        bot = Mock()
        bot.send_photo.return_value = SimpleNamespace(
            chat=SimpleNamespace(id=STAFF_ARCHIVE_VENUES[6]["channel_id"]), message_id=20055,
            photo=[object()], caption=channel.caption(entry, 6), reply_markup=markup,
        )
        cur, conn = Mock(), Mock()
        self.assertTrue(channel._send_post(bot, "example_bot", cur, conn, entry, venue_id=6))
        bot.send_photo.assert_called_once()
        self.assertEqual(bot.send_photo.call_args.args[0], STAFF_ARCHIVE_VENUES[6]["channel_id"])
        self.assertIn("staff_6_8", bot.send_photo.call_args.kwargs["reply_markup"]
                      .to_dict()["inline_keyboard"][0][0]["url"])

    def test_wrong_edit_response_does_not_replace_navigation_id(self):
        cur, conn = Mock(), Mock()
        result = SimpleNamespace(message_id=500)
        channel._request(cur, conn, "dalang_archive_pages", "page_number", 1,
                         lambda: result, lambda _: False, editing=True)
        sql, args = cur.execute.call_args_list[-1].args
        self.assertNotIn("message_id=", sql)
        self.assertEqual(args[-1], 1)

    @patch.object(channel, "get_conn")
    @patch.object(channel, "listing")
    @patch.object(channel, "_send_post")
    def test_only_new_visible_person_is_queued_for_publication(self, send_post, listing, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [
            {"acquired": 1}, {"enabled": 1, "ready": 1},
            {"status": "sent", "message_id": 20045},
        ]
        cur.fetchall.return_value = [
            {"staff_id": 1, "status": "sent", "message_id": 20001},
            {"staff_id": 2, "status": "pending", "message_id": None},
            {"staff_id": 3, "status": "pending", "message_id": None},
        ]
        listing.return_value = {"members": [member(1), member(2)]}
        channel.run_once(Mock(), "example_bot")
        self.assertEqual(send_post.call_args.args[-1]["id"], 2)
        inserted = cur.executemany.call_args.args[1]
        self.assertEqual(set(inserted), {(1,), (2,)})
        self.assertNotIn((3,), inserted)

    @patch.object(channel, "get_conn")
    @patch.object(channel, "listing")
    @patch.object(channel, "_send_page")
    def test_price_review_blocks_existing_navigation_edits(self, send_page, listing, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [
            {"acquired": 1}, {"enabled": 1, "ready": 1},
            {"status": "sent", "message_id": 20045},
        ]
        cur.fetchall.side_effect = [
            [{"staff_id": 1, "status": "sent", "message_id": 20001}],
            [{"revision": 8}],
        ]
        listing.return_value = {"members": [member(1)]}
        channel.run_once(Mock(), "example_bot")
        send_page.assert_not_called()
        self.assertTrue(any("venue_price_channel_review" in call.args[0]
                            for call in cur.execute.call_args_list))

    @patch.object(channel, "get_conn")
    @patch.object(channel, "listing")
    @patch.object(channel, "_send_page")
    def test_navigation_can_update_when_no_price_review_is_pending(self, send_page, listing, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [
            {"acquired": 1}, {"enabled": 1, "ready": 1},
            {"status": "sent", "message_id": 20045},
        ]
        cur.fetchall.side_effect = [
            [{"staff_id": 1, "status": "sent", "message_id": 20001}],
            [],
            [{"page_number": 1, "status": "sent", "message_id": 20045, "content_hash": "old"}],
        ]
        listing.return_value = {"members": [member(1)]}
        channel.run_once(Mock(), "example_bot")
        send_page.assert_called_once()

    @patch.object(channel, "get_conn")
    def test_interrupted_send_is_recovered_as_uncertain_even_when_disabled(self, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [{"acquired": 1}, {"enabled": 0, "ready": 1}]
        channel.run_once(Mock(), "example_bot")
        queries = [call.args[0] for call in cur.execute.call_args_list]
        self.assertTrue(any("dalang_archive_posts SET status='uncertain'" in sql for sql in queries))
        self.assertTrue(any("dalang_archive_pages SET status='uncertain'" in sql for sql in queries))
        self.assertTrue(any("status='deleting'" in sql for sql in queries))
        self.assertTrue(any("status='uncertain'" in sql and "status='editing'" in sql
                            for sql in queries))
        self.assertTrue(any("RELEASE_LOCK" in sql for sql in queries))

    @patch.object(channel, "get_conn")
    def test_each_venue_uses_separate_archive_state_and_lock(self, get_conn):
        conn = get_conn.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        for venue_id, config in STAFF_ARCHIVE_VENUES.items():
            cur.execute.reset_mock()
            cur.fetchone.side_effect = [
                {"acquired": 1},
                {"enabled": 0, "ready": 1},
            ]
            channel.run_once(Mock(), "example_bot", venue_id)
            sql = [call.args[0] for call in cur.execute.call_args_list]
            self.assertEqual(cur.execute.call_args_list[0].args[1],
                             (f"{config['table_prefix']}_channel",))
            self.assertTrue(any(f"FROM {config['table_prefix']}_policy" in query for query in sql))
            self.assertTrue(any(f"UPDATE {config['table_prefix']}_posts" in query for query in sql))
            self.assertTrue(any(f"UPDATE {config['table_prefix']}_pages" in query for query in sql))


if __name__ == "__main__":
    unittest.main()
