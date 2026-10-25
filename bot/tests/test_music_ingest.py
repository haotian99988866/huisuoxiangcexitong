from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telethon import errors, types

import music
import music_ingest as ingest
from music_sources import DYNAMIC_HISTORY_SOURCES, SOURCES, external_forward


SOURCE = next(iter(SOURCES))


def audio(ident, title="Song", artist="Artist", video=False, voice=False, text=""):
    attributes = [types.DocumentAttributeAudio(duration=180, voice=voice, title=title, performer=artist),
                  types.DocumentAttributeFilename("song.m4a")]
    if video:
        attributes.append(types.DocumentAttributeVideo(duration=180, w=320, h=240))
    return SimpleNamespace(id=ident, document=SimpleNamespace(
        id=10000 + ident, size=1200, attributes=attributes, mime_type="audio/mp4"),
        video=video, voice=voice, message=text, noforwards=False, date=datetime.now(timezone.utc))


class MusicIngestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "import.sqlite3"
        self.store = ingest.MusicStore(self.path)
        self.addCleanup(self.store.close)
        self.source = SimpleNamespace(id=SOURCE, noforwards=False)
        self.target = SimpleNamespace(id=2368924969)
        self.user = SimpleNamespace(get_entity=AsyncMock(return_value=self.source),
                                    get_messages=AsyncMock(return_value=[audio(1)]),
                                    send_file=AsyncMock(return_value=SimpleNamespace(id=500)))
        self.bot = SimpleNamespace(get_messages=AsyncMock(return_value=[audio(500)]))

    def history(self, messages):
        async def iterate(source, **kwargs):
            selected = [m for m in messages if m.id > kwargs["min_id"]
                        and m.id < kwargs.get("max_id", 100000)]
            for message in selected[:kwargs["limit"]]:
                yield message
        self.user.iter_messages = iterate

    def test_explicit_enable_and_persisted_progress(self):
        self.assertFalse(self.store.enabled())
        self.store.enable()
        self.store.progress(SOURCE, 100)
        self.store.advance(SOURCE, 22, True)
        self.store.advance(SOURCE, 110, False)
        another = ingest.MusicStore(self.path)
        try:
            self.assertTrue(another.enabled())
            row = another.progress(SOURCE, 999)
            self.assertEqual((row["boundary"], row["history"], row["live"]), (100, 22, 110))
        finally:
            another.close()
        self.store.disable()
        self.assertFalse(self.store.enabled())

    def test_daily_boundary_uses_utc_plus_seven(self):
        class Clock(datetime):
            moment = datetime(2026, 1, 1, 16, 59, tzinfo=timezone.utc)

            @classmethod
            def now(cls, tz=None):
                return cls.moment.astimezone(tz)

        with patch.object(ingest, "datetime", Clock):
            self.assertEqual(ingest.day_key(), "2026-01-01")
            Clock.moment += timedelta(minutes=1)
            self.assertEqual(ingest.day_key(), "2026-01-02")

    async def test_historical_quota_is_exactly_50_audio_per_source(self):
        self.store.progress(SOURCE, 200)
        self.history([audio(i) for i in range(1, 101)])
        await ingest.scan_source(self.user, self.source, self.store, historical=True)
        jobs = self.store.connection.execute("SELECT message FROM jobs ORDER BY message").fetchall()
        self.assertEqual([r["message"] for r in jobs], list(range(1, 51)))
        self.assertEqual(self.store.progress(SOURCE, 999)["history"], 50)
        await ingest.scan_source(self.user, self.source, self.store, historical=True)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 50)
        other = list(SOURCES)[1]
        self.store.progress(other, 200)
        await ingest.scan_source(self.user, SimpleNamespace(id=other), self.store, historical=True)
        self.assertEqual(self.store.remaining(other, ingest.day_key()), 0)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 100)

    async def test_happycat_history_has_queue_bound_but_no_daily_cap(self):
        source = next(iter(DYNAMIC_HISTORY_SOURCES))
        self.assertEqual(SOURCES[source][0], "happycat03")
        self.store.progress(source, 200)
        self.history([audio(i) for i in range(1, 151)])
        channel = SimpleNamespace(id=source)
        await ingest.scan_source(self.user, channel, self.store, historical=True)
        self.assertEqual(self.store.remaining(source, ingest.day_key()), 0)
        self.assertEqual(self.store.progress(source, 0)["history"], 50)
        with self.store.connection:
            self.store.connection.execute("UPDATE jobs SET status='sent' WHERE source=?", (source,))
        await ingest.scan_source(self.user, channel, self.store, historical=True)
        self.assertEqual(self.store.connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE source=?", (source,)).fetchone()[0], 100)
        self.assertEqual(self.store.progress(source, 0)["history"], 100)
        self.assertEqual(self.store.remaining(source, ingest.day_key()), 0)

    def test_flood_wait_deadline_survives_restart_and_cannot_be_shortened(self):
        with patch.object(ingest.time, "time", return_value=100):
            self.store.defer(60)
            self.store.defer(10)
        restarted = ingest.MusicStore(self.path)
        try:
            with patch.object(ingest.time, "time", return_value=120):
                self.assertEqual(restarted.wait_remaining(), 41)
            with patch.object(ingest.time, "time", return_value=162):
                self.assertEqual(restarted.wait_remaining(), 0)
        finally:
            restarted.close()

    async def test_explicit_flood_rejection_requeues_single_audio_request(self):
        self.store.enqueue(SOURCE, 1, True, ingest.day_key())
        self.user.send_file.side_effect = errors.FloodWaitError(request=None, capture=30)
        with self.assertRaises(errors.FloodWaitError):
            await ingest.process_job(self.user, self.bot, {SOURCE: self.source},
                                     self.target, self.store, self.store.pending())
        self.assertEqual(self.store.pending()["status"], "queued")

    async def test_happycat_live_prioritized_without_history_quota(self):
        source = next(iter(DYNAMIC_HISTORY_SOURCES))
        self.store.progress(source, 50)
        self.history([audio(i) for i in range(1, 56)])
        channel = SimpleNamespace(id=source)
        await ingest.scan_source(self.user, channel, self.store, historical=True)
        await ingest.scan_source(self.user, channel, self.store, historical=False)
        self.assertEqual(self.store.pending()["historical"], 0)
        self.assertEqual(self.store.pending()["message"], 51)
        self.assertTrue(external_forward(SimpleNamespace(
            forward_from_chat=SimpleNamespace(id=int(f"-100{source}")))))

    async def test_live_is_not_limited_by_historical_quota_or_mixed_with_baseline(self):
        self.store.progress(SOURCE, 50)
        self.history([audio(i) for i in range(1, 106)])
        await ingest.scan_source(self.user, self.source, self.store, historical=True)
        await ingest.scan_source(self.user, self.source, self.store, historical=False)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 105)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM jobs WHERE historical=0").fetchone()[0], 55)
        await ingest.scan_source(self.user, self.source, self.store, historical=False)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 105)

    async def test_oldest_first_ignores_video_voice_ads_and_non_audio(self):
        self.store.progress(SOURCE, 20)
        self.history([audio(1, video=True), audio(2, voice=True), audio(3, text="#付费广告\nSong"),
                      SimpleNamespace(id=4, document=None), audio(5)])
        await ingest.scan_source(self.user, self.source, self.store, historical=True)
        jobs = self.store.connection.execute("SELECT message FROM jobs").fetchall()
        self.assertEqual([r["message"] for r in jobs], [5])
        self.assertTrue(self.store.progress(SOURCE, 999)["complete"])

    async def test_eight_minute_limit_is_inclusive_and_long_tracks_do_not_use_quota(self):
        self.store.progress(SOURCE, 10)
        allowed = audio(1)
        allowed.document.attributes[0].duration = 480
        too_long = audio(2)
        too_long.document.attributes[0].duration = 481
        missing = audio(3)
        missing.document.attributes = [types.DocumentAttributeFilename("song.m4a")]
        self.history([allowed, too_long, missing])
        await ingest.scan_source(self.user, self.source, self.store, historical=True)
        self.assertEqual([row["message"] for row in self.store.connection.execute("SELECT message FROM jobs")], [1])
        self.assertEqual(self.store.remaining(SOURCE, ingest.day_key()), 49)
        self.assertIsNone(ingest.audio_metadata(too_long))
        self.assertIsNone(ingest.audio_metadata(missing))

    async def test_queued_track_that_becomes_too_long_is_not_forwarded(self):
        self.store.enqueue(SOURCE, 1, True, ingest.day_key())
        message = audio(1)
        message.document.attributes[0].duration = 481
        self.user.get_messages.return_value = [message]
        await ingest.process_job(self.user, self.bot, {SOURCE: self.source}, self.target,
                                 self.store, self.store.pending())
        self.user.send_file.assert_not_awaited()
        self.assertIsNone(self.store.pending())
        self.assertEqual(self.store.remaining(SOURCE, ingest.day_key()), 50)

    def test_invalid_duration_is_not_treated_as_zero_or_rounded_down(self):
        for duration in (None, 0, -1, True, "180", float("nan"), float("inf"), 480.1):
            with self.subTest(duration=duration):
                message = audio(1)
                message.document.attributes[0].duration = duration
                self.assertIsNone(ingest.audio_metadata(message))
        message = audio(1)
        message.document.attributes[0].duration = 479.9
        self.assertIsNotNone(ingest.audio_metadata(message))

    async def test_forwarded_long_audio_is_not_indexed_or_forwarded_again(self):
        self.store.enqueue(SOURCE, 1, True, ingest.day_key())
        self.store.update(self.store.pending(), "forwarded", result_id=500)
        message = audio(500)
        message.document.attributes[0].duration = 481
        self.bot.get_messages.return_value = [message]
        with patch.object(ingest, "register_track") as register:
            await ingest.process_job(self.user, self.bot, {SOURCE: self.source}, self.target,
                                     self.store, self.store.pending())
        register.assert_not_called()
        self.user.send_file.assert_not_awaited()
        self.assertIsNone(self.store.pending())
        self.assertEqual(self.store.remaining(SOURCE, ingest.day_key()), 50)

    async def test_pending_reservations_survive_midnight_without_exceeding_daily_quota(self):
        for ident in range(1, 51):
            self.store.enqueue(SOURCE, ident, True, "2000-01-01")
        self.assertEqual(self.store.remaining(SOURCE, "2000-01-02"), 0)
        with patch.object(ingest, "day_key", return_value="2000-01-02"):
            job = self.store.pending()
            self.store.record_send_day(job)
            self.store.update(job, "sent", result_id=500)
        self.assertEqual(self.store.remaining(SOURCE, "2000-01-02"), 0)
        for row in self.store.connection.execute("SELECT * FROM jobs").fetchall():
            with patch.object(ingest, "day_key", return_value="2000-01-02"):
                self.store.record_send_day(row)
            self.store.update(row, "sent")
        self.assertEqual(self.store.remaining(SOURCE, "2000-01-03"), 50)

    async def test_forward_success_index_retries_do_not_forward_twice(self):
        self.store.enqueue(SOURCE, 1, True, ingest.day_key())
        await ingest.process_job(self.user, self.bot, {SOURCE: self.source}, self.target, self.store, self.store.pending())
        self.assertEqual(self.store.pending()["status"], "forwarded")
        with patch.object(ingest, "register_track", side_effect=RuntimeError("DB offline")):
            with self.assertRaises(RuntimeError):
                await ingest.process_job(self.user, self.bot, {SOURCE: self.source}, self.target,
                                         self.store, self.store.pending())
        with patch.object(ingest, "register_track", return_value=42):
            await ingest.process_job(self.user, self.bot, {SOURCE: self.source}, self.target,
                                     self.store, self.store.pending())
        self.user.send_file.assert_awaited_once()
        self.assertIsNone(self.store.pending())
        self.assertEqual(self.store.connection.execute("SELECT track_id FROM jobs").fetchone()[0], 42)

    async def test_same_document_across_channels_stored_only_once(self):
        self.store.enqueue(SOURCE, 1, True, ingest.day_key())
        self.store.update(self.store.pending(), "sent", result_id=500, track_id=42, document_id=10001)
        other = list(SOURCES)[1]
        self.store.enqueue(other, 1, True, ingest.day_key())
        await ingest.process_job(self.user, self.bot, {other: self.source}, self.target, self.store, self.store.pending())
        self.user.send_file.assert_not_awaited()
        self.assertEqual(self.store.connection.execute("SELECT track_id FROM jobs WHERE source=?", (other,)).fetchone()[0], 42)

    async def test_unknown_forward_outcome_stops_automatic_retries_after_restart(self):
        self.store.enqueue(SOURCE, 1, True, ingest.day_key())
        self.user.send_file.side_effect = ConnectionError("offline")
        with self.assertRaises(ConnectionError):
            await ingest.process_job(self.user, self.bot, {SOURCE: self.source}, self.target, self.store, self.store.pending())
        observer = ingest.MusicStore(self.path)
        try:
            self.assertEqual(observer.connection.execute("SELECT status FROM jobs").fetchone()[0], "sending")
        finally:
            observer.close()
        restarted = ingest.MusicStore(self.path, recover=True)
        try:
            self.assertIsNone(restarted.pending())
            self.assertEqual(restarted.connection.execute("SELECT status FROM jobs").fetchone()[0], "uncertain")
        finally:
            restarted.close()

    async def test_protected_source_never_forwarded(self):
        self.store.enqueue(SOURCE, 1, True, ingest.day_key())
        self.source.noforwards = True
        await ingest.process_job(self.user, self.bot, {SOURCE: self.source}, self.target, self.store, self.store.pending())
        self.user.send_file.assert_not_awaited()
        self.assertIsNone(self.store.pending())

    def test_forward_origin_only_matches_authorized_channels(self):
        forwarded = SimpleNamespace(forward_origin=SimpleNamespace(chat=SimpleNamespace(id=int(f"-100{SOURCE}"))))
        self.assertTrue(external_forward(forwarded))
        self.assertFalse(external_forward(SimpleNamespace()))
        self.assertFalse(external_forward(SimpleNamespace(forward_from_chat=SimpleNamespace(id=-1001))))

    def test_nested_forward_is_claimed_before_target_id_is_known(self):
        self.store.enqueue(SOURCE, 1, True, ingest.day_key())
        job = self.store.pending()
        self.store.update(job, "sending", document_id=10001)
        message = SimpleNamespace(message_id=500, forward_origin=SimpleNamespace(chat=SimpleNamespace(id=-1001)),
                                  audio=SimpleNamespace(file_id="file-id"))
        with patch.object(ingest.utils, "resolve_bot_file_id", return_value=SimpleNamespace(id=10001)):
            self.assertTrue(external_forward(message, self.path))
        with patch.object(ingest.utils, "resolve_bot_file_id", return_value=SimpleNamespace(id=20002)):
            self.assertFalse(external_forward(message, self.path))
        self.store.update(job, "sent", result_id=500)
        with patch.object(ingest.utils, "resolve_bot_file_id") as decode:
            self.assertTrue(external_forward(message, self.path))
        decode.assert_not_called()

    def test_excluding_previously_sent_audio_preserves_quota_and_history(self):
        self.store.enqueue(SOURCE, 1, True, ingest.day_key())
        self.store.update(self.store.pending(), "excluded", result_id=500, track_id=42, document_id=10001)
        self.assertIsNone(self.store.pending())
        self.assertEqual(self.store.remaining(SOURCE, ingest.day_key()), 49)
        self.assertTrue(self.store.enqueue(SOURCE, 1, True, ingest.day_key()))
        self.assertIsNone(self.store.pending())
        message = SimpleNamespace(message_id=500, forward_origin=SimpleNamespace())
        self.assertTrue(external_forward(message, self.path))

    def test_lazy_register_uses_owned_copy_and_does_not_download(self):
        conn = MagicMock()
        from contextlib import nullcontext
        with patch("db.get_conn", return_value=conn), patch("music_dedupe.import_lock", return_value=nullcontext()), \
                patch("music_dedupe.save_track", return_value=(42, True)) as save, \
                patch.object(ingest.utils, "pack_bot_file_id", return_value="bot-file-id"):
            self.assertEqual(ingest.register_track(audio(500), SOURCE, 1), 42)
        self.assertNotIn("download", save.call_args.kwargs)
        self.assertEqual(save.call_args.kwargs["channel_id"], music.MUSIC_CHANNEL_ID)
        self.assertEqual(save.call_args.kwargs["message_id"], 500)
        self.assertEqual(save.call_args.kwargs["file_id"], "bot-file-id")
        conn.commit.assert_called_once()

    async def test_clean_copy_contains_only_music_metadata_without_contacts_or_buttons(self):
        item = audio(1, title="Song | @contact", artist="Artist 商务合作 @agent",
                     text="广告链接 https://t.me/example\n微信:123456789")
        self.user.get_messages.return_value = [item]
        self.store.enqueue(SOURCE, 1, True, ingest.day_key())
        await ingest.process_job(self.user, self.bot, {SOURCE: self.source},
                                 self.target, self.store, self.store.pending())
        self.user.send_file.assert_awaited_once_with(
            self.target, item.document, caption="Song\nArtist",
            parse_mode=None, formatting_entities=[], silent=True)

    def test_contact_fields_clean_without_removing_regular_music_titles(self):
        for value in ("Song @contact", "Song https://t.me/contact", "Song 联系方式 +8613812345678",
                      "Song test@example.com", "Song +86 13812345678", "Song\n广告"):
            self.assertEqual(ingest.clean_music_field(value), "Song")
        for value in ("爱的联系", "Call Me Maybe", "867-5309", "五月天"):
            self.assertEqual(ingest.clean_music_field(value), value)

    def test_nonforwarded_collector_copy_claimed_before_publication_returns(self):
        self.store.enqueue(SOURCE, 1, True, ingest.day_key())
        self.store.update(self.store.pending(), "sending", document_id=10001)
        message = SimpleNamespace(message_id=500, audio=SimpleNamespace(file_id="file-id"))
        with patch.object(ingest.utils, "resolve_bot_file_id", return_value=SimpleNamespace(id=10001)):
            self.assertTrue(external_forward(message, self.path))
        with patch.object(ingest.utils, "resolve_bot_file_id", return_value=SimpleNamespace(id=99999)):
            self.assertFalse(external_forward(message, self.path))
