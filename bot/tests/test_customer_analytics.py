import unittest
from unittest.mock import Mock, patch

import customer_analytics as analytics


class CustomerAnalyticsTests(unittest.TestCase):
    def setup_cursor(self, responses):
        connection = Mock()
        cursor = Mock()
        connection.cursor.return_value.__enter__ = Mock(return_value=cursor)
        connection.cursor.return_value.__exit__ = Mock(return_value=False)
        cursor.fetchone.side_effect = responses
        return connection, cursor

    def test_duplicate_heartbeat_does_not_add_duration(self):
        user = {"id": 123}
        session, view_id = "a" * 32, "b" * 32
        view = {"user_id": 123, "session_id": session, "page_key": "music",
                "client_ms": 15000, "elapsed": 15}
        connection, cursor = self.setup_cursor([
            {"first_seen": None}, {"recording": True}, {"user_id": 123}, view,
        ])
        with patch.object(analytics, "get_conn", return_value=connection), \
                patch.object(analytics, "admin_ids", return_value=set()):
            analytics.record(user, {"session_id": session, "view_id": view_id,
                                   "page_key": "music", "total_ms": 15000}, "")
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertFalse(any("active_ms=active_ms+" in sql for sql in statements))
        connection.commit.assert_called_once()

    def test_session_cannot_be_transferred_to_another_customer(self):
        connection, _ = self.setup_cursor([
            {"first_seen": None}, {"recording": True}, {"user_id": 999},
        ])
        with patch.object(analytics, "get_conn", return_value=connection), \
                patch.object(analytics, "admin_ids", return_value=set()):
            with self.assertRaises(ValueError):
                analytics.record({"id": 123}, {
                    "session_id": "a" * 32, "view_id": "b" * 32,
                    "page_key": "music", "total_ms": 0,
                }, "")
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()

    def test_signed_invitation_payload_is_extracted_only_for_referral(self):
        self.assertEqual(analytics.invitation_token("start_param=ref_" + "a" * 32), "a" * 32)
        self.assertIsNone(analytics.invitation_token("start_param=music"))

    def test_invalid_page_or_time_rejected_before_database_access(self):
        with patch.object(analytics, "get_conn") as connect:
            for page, total in [([], 0), ("music", -1), ("music", True)]:
                with self.assertRaises(ValueError):
                    analytics.record({"id": 123}, {
                        "session_id": "a" * 32, "view_id": "b" * 32,
                        "page_key": page, "total_ms": total,
                    }, "")
            connect.assert_not_called()
