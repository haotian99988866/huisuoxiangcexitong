from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from operations_alerts import (
    ALERT_AFTER_SECONDS, REPEAT_SECONDS, AlertStore,
    check_once, component_issues, send_due, start_worker,
)


def component(state="active", counts=None, error_at=None):
    return {
        "key": "test_worker", "name": "Test worker", "enabled": True, "state": state,
        "counts": counts or {}, "last_error_at": error_at,
        "last_error_code": "ConnectionError" if error_at else None,
    }


class OperationsAlertsTests(unittest.TestCase):
    def setUp(self):
        admins = patch("visit_stats.admin_ids", return_value={1})
        admins.start()
        self.addCleanup(admins.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "alerts.sqlite3"
        self.store = AlertStore(self.path)
        self.addCleanup(self.store.close)
        self.bot = MagicMock()
        self.issue = {"test:worker": ("Test worker", "No heartbeat", None)}

    def test_five_minute_threshold_and_thirty_minute_repeats(self):
        self.store.observe(self.issue, 1000)
        send_due(self.bot, 1, self.store, 1000 + ALERT_AFTER_SECONDS - 1)
        self.bot.send_message.assert_not_called()
        send_due(self.bot, 1, self.store, 1000 + ALERT_AFTER_SECONDS)
        self.assertEqual(self.bot.send_message.call_count, 1)
        send_due(self.bot, 1, self.store, 1000 + ALERT_AFTER_SECONDS + REPEAT_SECONDS - 1)
        self.assertEqual(self.bot.send_message.call_count, 1)
        send_due(self.bot, 1, self.store, 1000 + ALERT_AFTER_SECONDS + REPEAT_SECONDS)
        self.assertEqual(self.bot.send_message.call_count, 2)

    def test_transient_problem_does_not_alert_and_recovery_is_sent_once(self):
        self.store.observe(self.issue, 1000)
        self.store.observe({}, 1100)
        send_due(self.bot, 1, self.store, 1300)
        self.bot.send_message.assert_not_called()
        self.store.observe(self.issue, 1400)
        send_due(self.bot, 1, self.store, 1700)
        self.store.observe({}, 1730)
        send_due(self.bot, 1, self.store, 1730)
        self.assertEqual(self.bot.send_message.call_count, 2)
        send_due(self.bot, 1, self.store, 1760)
        self.assertEqual(self.bot.send_message.call_count, 2)
        self.assertEqual([row[0] for row in self.store.connection.execute(
            "SELECT kind FROM deliveries ORDER BY id")], ["alert", "recovery"])

    def test_restart_preserves_first_observed_time_and_repeat_deadline(self):
        self.store.observe(self.issue, 1000)
        send_due(self.bot, 1, self.store, 1300)
        observer = AlertStore(self.path)
        try:
            observer.observe(self.issue, 1400)
            send_due(self.bot, 1, observer, 1400)
            self.assertEqual(self.bot.send_message.call_count, 1)
            self.assertEqual(observer.connection.execute(
                "SELECT since FROM incidents").fetchone()[0], 1000)
        finally:
            observer.close()

    def test_unsupported_or_disabled_components_and_old_errors_do_not_alert(self):
        now = 1000
        old_error = datetime.fromtimestamp(100, timezone.utc).isoformat()
        self.assertEqual(component_issues(component(error_at=old_error), now), {})
        disabled = component("disabled", {"failed": 50, "queued": 100})
        disabled["enabled"] = False
        self.assertEqual(component_issues(disabled, now), {})
        fresh = datetime.fromtimestamp(990, timezone.utc).isoformat()
        self.assertIn("test_worker:errors", component_issues(component(error_at=fresh), now))

    def test_stopped_workers_failed_uncertain_jobs_and_backlog_are_monitored(self):
        issues = component_issues(component("stale", {"failed": 2, "uncertain": 1, "queued": 100}), 1000)
        self.assertEqual(set(issues), {"test_worker:worker", "test_worker:jobs", "test_worker:queue"})
        self.assertNotIn("test_worker:jobs", component_issues(component(counts={"sent": 500}), 1000))

    def test_backlog_progress_resets_stall_timer_and_notifies_recovery(self):
        first = component_issues(component(counts={"queued": 100, "ready": 1}), 1000)
        next_state = component_issues(component(counts={"queued": 99, "ready": 2}), 1200)
        self.store.observe(first, 1000)
        self.store.observe(next_state, 1200)
        send_due(self.bot, 1, self.store, 1300)
        self.bot.send_message.assert_not_called()
        send_due(self.bot, 1, self.store, 1500)
        self.assertEqual(self.bot.send_message.call_count, 1)
        progress = component_issues(component(counts={"queued": 98, "ready": 3}), 1530)
        self.store.observe(progress, 1530)
        send_due(self.bot, 1, self.store, 1530)
        self.assertEqual(self.bot.send_message.call_count, 2)
        self.assertEqual(self.store.connection.execute(
            "SELECT kind FROM deliveries ORDER BY id DESC LIMIT 1").fetchone()[0], "recovery")

    def test_incoming_jobs_and_new_failures_do_not_hide_stalled_backlog(self):
        self.store.observe(component_issues(component(counts={"queued": 100, "failed": 1}), 1000), 1000)
        self.store.observe(component_issues(component(counts={"queued": 120, "failed": 2}), 1200), 1200)
        send_due(self.bot, 1, self.store, 1300)
        keys = self.store.connection.execute("SELECT keys FROM deliveries").fetchone()[0]
        self.assertIn("test_worker:queue", keys)
        self.assertIn("test_worker:jobs", keys)

    def test_recent_repeated_errors_with_heartbeat_alert_but_old_error_recovers(self):
        for now in range(1000, 1301, 30):
            error_at = datetime.fromtimestamp(now, timezone.utc).isoformat()
            self.store.observe(component_issues(component(error_at=error_at), now), now)
            send_due(self.bot, 1, self.store, now)
        self.assertEqual(self.bot.send_message.call_count, 1)
        self.store.observe(component_issues(component(error_at=error_at), 1481), 1481)
        send_due(self.bot, 1, self.store, 1481)
        self.assertEqual(self.bot.send_message.call_count, 2)

    def test_revoked_recipient_does_not_receive_any_notification(self):
        with patch("visit_stats.admin_ids", return_value=set()):
            with self.assertRaises(ValueError):
                check_once(self.bot, 1, self.store, 1000)
        self.bot.send_message.assert_not_called()

    def test_index_query_failure_marks_component_unavailable(self):
        from operations_health import _announcement_pending
        db = sqlite3.connect(self.path)
        try:
            db.execute("CREATE TABLE jobs(source INTEGER,target TEXT,job_key TEXT,status TEXT)")
            db.execute("INSERT INTO jobs VALUES(1,'2','10','sent')")
            db.commit()
        finally:
            db.close()
        item = component()
        item["note"] = None
        with patch("operations_health.get_conn", side_effect=ValueError("unavailable")), \
                patch("operations_health.logger.exception"):
            _announcement_pending(item, self.path)
        self.assertEqual(item["state"], "unavailable")
        self.assertIn("test_worker:worker", component_issues(item, 1000))

    def test_snapshot_failure_is_alerted_without_falsely_recovering_other_incidents(self):
        self.store.observe(self.issue, 1000)
        send_due(self.bot, 1, self.store, 1300)
        with patch("operations_alerts.snapshot", side_effect=RuntimeError("offline")), \
                patch("operations_alerts.logger.exception"):
            check_once(self.bot, 1, self.store, 1400)
            check_once(self.bot, 1, self.store, 1700)
        self.assertEqual(self.store.connection.execute(
            "SELECT active FROM incidents WHERE key='test:worker'").fetchone()[0], 1)
        self.assertEqual(self.bot.send_message.call_count, 2)

    def test_notification_timeout_is_persisted_and_not_blindly_retried(self):
        self.store.observe(self.issue, 1000)
        self.bot.send_message.side_effect = TimeoutError("uncertain delivery")
        with patch("operations_alerts.logger.error"):
            send_due(self.bot, 1, self.store, 1300)
            send_due(self.bot, 1, self.store, 1330)
        self.assertEqual(self.bot.send_message.call_count, 1)
        row = self.store.connection.execute("SELECT status,error_code FROM deliveries").fetchone()
        self.assertEqual(tuple(row), ("uncertain", "TimeoutError"))

    def test_persisted_recovery_survives_restart_before_notification(self):
        self.store.observe(self.issue, 1000)
        send_due(self.bot, 1, self.store, 1300)
        self.store.observe({}, 1330)
        observer = AlertStore(self.path)
        try:
            observer.observe(self.issue, 1360)
            send_due(self.bot, 1, observer, 1360)
            self.assertEqual(self.bot.send_message.call_count, 2)
            self.assertEqual(observer.connection.execute(
                "SELECT kind FROM deliveries ORDER BY id DESC LIMIT 1").fetchone()[0], "recovery")
        finally:
            observer.close()

    def test_many_alerts_are_batched_and_no_business_retry_is_called(self):
        issues = {f"worker_{index}": (f"Worker {index}", "Unhealthy", None) for index in range(25)}
        self.store.observe(issues, 1000)
        send_due(self.bot, 1, self.store, 1300)
        self.assertEqual(self.bot.send_message.call_count, 3)
        for call in self.bot.send_message.call_args_list:
            self.assertLess(len(call.args[1]), 4096)

    def test_start_does_not_block_bot_or_create_duplicate_monitor_threads(self):
        with patch("operations_alerts._started", False), \
                patch("operations_alerts.threading.Thread") as thread:
            start_worker(self.bot)
            start_worker(self.bot)
            thread.assert_called_once()
            thread.return_value.start.assert_called_once()
        self.bot.get_chat.assert_not_called()


if __name__ == "__main__":
    unittest.main()
