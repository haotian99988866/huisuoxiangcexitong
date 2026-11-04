from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telethon import types

from channel_sync import JobStore, deliver
from news_dedupe import (
    MIN_SIMILAR_LENGTH, WINDOW_SECONDS, media_digest,
    normalized_body, seed_recent_news, similar_body,
)


class MatchingTests(unittest.TestCase):
    def test_normalization_keeps_decimal_numbers_and_rates(self):
        self.assertEqual(normalized_body("ＡＢＣ　新闻\n正文"), "abc新闻正文")
        base = "现场调查情况通报警方正在调查事故原因请关注后续消息" * 5
        for first, second in [("1.5人", "15人"), ("5%", "5"), ("-5", "+5")]:
            self.assertFalse(similar_body(normalized_body(base + first),
                                          normalized_body(base + second)))

    def test_similarity_threshold_and_minimum_length(self):
        base = "abcdefghij" * 10
        self.assertTrue(similar_body(base, base[:-5] + "zzzzz"))
        self.assertFalse(similar_body(base, base[:-6] + "zzzzzz"))
        self.assertFalse(similar_body("短新闻", "短消息"))
        self.assertTrue(similar_body("短新闻", "短新闻"))
        self.assertEqual(MIN_SIMILAR_LENGTH, 80)
        self.assertEqual(WINDOW_SECONDS, 54000)

    def test_media_digest_uses_full_bytes_and_complete_album_not_filenames(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            first, same, other = [folder / name for name in ("first.jpg", "renamed.bin", "other.jpg")]
            first.write_bytes(b"first file")
            same.write_bytes(b"first file")
            other.write_bytes(b"different bytes")
            self.assertEqual(media_digest([first]), media_digest([same]))
            self.assertNotEqual(media_digest([first]), media_digest([other]))
            self.assertNotEqual(media_digest([first]), media_digest([first, other]))
            self.assertEqual(media_digest([first, other]), media_digest([other, first]))


class NewsDedupeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.store = JobStore(self.directory / "jobs.sqlite3")
        self.addCleanup(self.store.close)
        self.target = SimpleNamespace(id=2)
        self.user = SimpleNamespace(get_entity=AsyncMock(return_value=SimpleNamespace(noforwards=False)),
                                    get_messages=AsyncMock(), download_media=AsyncMock())
        self.bot = SimpleNamespace(get_messages=AsyncMock(), download_media=AsyncMock(),
                                   send_message=AsyncMock(return_value=SimpleNamespace(id=100)),
                                   send_file=AsyncMock(return_value=SimpleNamespace(id=101)))

    def job(self, source, number):
        self.store.enqueue(source, "2", [number])
        return self.store.next(source, "2")

    def message(self, body, media=None):
        return SimpleNamespace(message=body, entities=[], media=media, noforwards=False,
                               document=None, video=None, audio=None, gif=None)

    def remember(self, job, body, digest=None, status="sent", seen_at=None):
        self.store.update(job, status, result_ids=[100])
        with self.store.connection:
            self.store.news_deduper.remember(job, body, digest, time.time() if seen_at is None else seen_at)

    async def test_cross_source_similar_text_is_skipped_before_media_download(self):
        body = "police report continued investigation detailed witness statement traffic safety " * 3
        first = self.job(1, 10)
        self.remember(first, body)
        second = self.job(3, 10)
        self.user.get_messages.return_value = [self.message(body + "news", types.MessageMediaPhoto())]
        await deliver(self.user, self.bot, SimpleNamespace(id=3), self.target, self.store,
                      second, self.directory)
        self.user.download_media.assert_not_awaited()
        self.bot.send_file.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()
        self.assertEqual(self.store.connection.execute(
            "SELECT status FROM jobs WHERE source=3").fetchone()[0], "skipped")

    async def test_identical_media_without_caption_is_skipped_using_binary_hash(self):
        file = self.directory / "source.jpg"
        file.write_bytes(b"same photo bytes")
        first = self.job(1, 10)
        self.remember(first, "", media_digest([file]))
        second = self.job(3, 10)
        self.user.get_messages.return_value = [self.message("", types.MessageMediaPhoto())]
        self.user.download_media.return_value = str(file)
        await deliver(self.user, self.bot, SimpleNamespace(id=3), self.target, self.store,
                      second, self.directory)
        self.bot.send_file.assert_not_awaited()
        self.assertEqual(self.store.connection.execute(
            "SELECT status FROM jobs WHERE source=3").fetchone()[0], "skipped")

    async def test_identical_short_captions_with_different_photos_are_not_merged(self):
        file = self.directory / "source.jpg"
        file.write_bytes(b"new photo")
        first = self.job(1, 10)
        self.remember(first, "突发新闻", "different photo hash")
        second = self.job(3, 10)
        self.user.get_messages.return_value = [self.message("突发新闻", types.MessageMediaPhoto())]
        self.user.download_media.return_value = str(file)
        await deliver(self.user, self.bot, SimpleNamespace(id=3), self.target, self.store,
                      second, self.directory)
        self.bot.send_file.assert_awaited_once()
        self.assertEqual(self.store.connection.execute(
            "SELECT status FROM jobs WHERE source=3").fetchone()[0], "sent")

    def test_failed_claim_does_not_block_another_source_but_uncertain_claim_does(self):
        first, second = self.job(1, 10), self.job(3, 10)
        self.remember(first, "相同新闻", status="failed")
        self.assertIsNone(self.store.news_deduper.find(second, "相同新闻"))
        self.store.update(first, "uncertain", result_ids=[100])
        self.assertIsNotNone(self.store.news_deduper.find(second, "相同新闻"))
        self.store.update(first, "sending", result_ids=[100])
        self.assertIsNotNone(self.store.news_deduper.find(second, "相同新闻"))

    def test_fifteen_hour_expiry_and_restart_persistence(self):
        first, second = self.job(1, 10), self.job(3, 10)
        now = time.time()
        self.remember(first, "相同新闻", seen_at=now - WINDOW_SECONDS)
        with patch("news_dedupe.time.time", return_value=now):
            self.assertIsNotNone(self.store.news_deduper.find(second, "相同新闻"))
        with patch("news_dedupe.time.time", return_value=now + 0.01):
            self.assertIsNone(self.store.news_deduper.find(second, "相同新闻"))
        self.remember(first, "相同新闻")
        another = JobStore(self.directory / "jobs.sqlite3")
        try:
            self.assertIsNotNone(another.news_deduper.find(second, "相同新闻"))
        finally:
            another.close()

    def test_duplicates_between_five_and_fifteen_hours_are_suppressed(self):
        first, second = self.job(1, 10), self.job(3, 10)
        now = time.time()
        for hours in (5, 6, 10, 14.99, 15):
            with self.subTest(hours=hours):
                self.remember(first, "相同新闻", seen_at=now - hours * 3600)
                with patch("news_dedupe.time.time", return_value=now):
                    self.assertIsNotNone(self.store.news_deduper.find(second, "相同新闻"))
        self.remember(first, "相同新闻", seen_at=now - 15 * 3600 - 1)
        with patch("news_dedupe.time.time", return_value=now):
            self.assertIsNone(self.store.news_deduper.find(second, "相同新闻"))

    def test_earliest_source_timestamp_wins_among_currently_queued_jobs(self):
        self.store.enqueue(1, "2", [10], 200)
        self.store.enqueue(3, "2", [10], 100)
        self.assertEqual(self.store.next_news((1, 3), "2")["source"], 3)

    async def test_partial_send_keeps_fingerprint_and_suppresses_duplicates_after_recovery(self):
        body = "news" * 300
        first = self.job(1, 10)
        self.user.get_messages.return_value = [self.message(body, types.MessageMediaPhoto())]
        file = self.directory / "source.jpg"
        file.write_bytes(b"photo")
        self.user.download_media.return_value = str(file)
        self.bot.send_message.side_effect = ConnectionError("interrupted")
        with self.assertRaises(ConnectionError):
            await deliver(self.user, self.bot, SimpleNamespace(id=1), self.target, self.store,
                          first, self.directory)
        self.store.recover()
        second = self.job(3, 10)
        self.assertIsNotNone(self.store.news_deduper.find(second, body))

    async def test_seed_reads_recent_published_history_without_changing_or_sending_jobs(self):
        first = self.job(1, 10)
        self.store.update(first, "sent", result_ids=[100])
        self.bot.get_messages.return_value = [SimpleNamespace(
            message="此前新闻正文", entities=[], media=None, date=datetime.now(timezone.utc))]
        database = MagicMock()
        database.cursor.return_value.__enter__.return_value.fetchall.return_value = []
        with patch("db.get_conn", return_value=database):
            await seed_recent_news(self.bot, self.target, self.store, self.directory)
        second = self.job(3, 10)
        self.assertIsNotNone(self.store.news_deduper.find(second, "此前新闻正文"))
        self.bot.send_message.assert_not_awaited()
        self.bot.send_file.assert_not_awaited()
        self.assertEqual(self.store.connection.execute(
            "SELECT status FROM jobs WHERE source=1").fetchone()[0], "sent")
        self.bot.get_messages.reset_mock()
        with patch("db.get_conn", return_value=database):
            await seed_recent_news(self.bot, self.target, self.store, self.directory)
        self.bot.get_messages.assert_not_awaited()

    async def test_seed_missing_recent_message_is_an_explicit_error(self):
        first = self.job(1, 10)
        self.store.update(first, "sent", result_ids=[100])
        self.bot.get_messages.return_value = [None]
        database = MagicMock()
        database.cursor.return_value.__enter__.return_value.fetchall.return_value = []
        with patch("db.get_conn", return_value=database), self.assertRaisesRegex(ValueError, "unavailable"):
            await seed_recent_news(self.bot, self.target, self.store, self.directory)

    async def test_old_queued_job_published_recently_is_included_from_website_history(self):
        from news_sources import NEWS_SOURCES, announcement_job_key
        first = self.job(NEWS_SOURCES[0][1], 10)
        self.store.update(first, "sent", result_ids=[100])
        with self.store.connection:
            self.store.connection.execute("UPDATE jobs SET created='2020-01-01T00:00:00+00:00'")
        database = MagicMock()
        database.cursor.return_value.__enter__.return_value.fetchall.return_value = [
            {"job_key": announcement_job_key(first)}]
        self.bot.get_messages.return_value = [SimpleNamespace(
            message="近期发布的旧排队新闻", entities=[], media=None, date=datetime.now(timezone.utc))]
        with patch("db.get_conn", return_value=database):
            await seed_recent_news(self.bot, self.target, self.store, self.directory)
        second = self.job(3, 10)
        self.assertIsNotNone(self.store.news_deduper.find(second, "近期发布的旧排队新闻"))

    async def test_recent_pure_media_history_gets_full_binary_fingerprint(self):
        first = self.job(1, 10)
        self.store.update(first, "sent", result_ids=[100])
        database = MagicMock()
        database.cursor.return_value.__enter__.return_value.fetchall.return_value = []
        file = self.directory / "historical.jpg"
        file.write_bytes(b"historical image bytes")
        self.bot.download_media.return_value = str(file)
        self.bot.get_messages.return_value = [SimpleNamespace(
            message="", entities=[], media=types.MessageMediaPhoto(), date=datetime.now(timezone.utc))]
        with patch("db.get_conn", return_value=database):
            await seed_recent_news(self.bot, self.target, self.store, self.directory)
        second = self.job(3, 10)
        self.assertIsNotNone(self.store.news_deduper.find(second, "", media_digest([file])))
        file.write_bytes(b"other image bytes")
        self.assertIsNone(self.store.news_deduper.find(second, "", media_digest([file])))


if __name__ == "__main__":
    unittest.main()
