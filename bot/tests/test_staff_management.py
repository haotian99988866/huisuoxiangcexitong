import unittest
from unittest.mock import MagicMock, patch

import staff_management as management
from customer_records import RecordConflict


class StaffManagementTests(unittest.TestCase):
    def test_all_staff_management_details_are_batched_and_admin_scoped(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [{
            "id": ident, "employment": "在职", "circumstance": "", "revision": 3,
            "updated_at": None, "subscribed": ident == 1, "customer_authorized": False,
            "notification_status": "sent", "notification_published_at": "2026-11-04 10:00:00",
        } for ident in range(1, 62)]
        members = [{"id": ident, "price_usd": "100", "price_detail": "private"} for ident in range(1, 62)]
        with patch.object(management, "_admin") as admin, \
                patch.object(management, "get_conn", return_value=connection), \
                patch("staff_archive.listing", return_value={"members": members, "total": 61}) as listing:
            result = management.overview({"venue_id": 1, "mode": "all"}, 99)
        admin.assert_called_once_with(99)
        listing.assert_called_once_with({"venue_id": 1, "mode": "all"}, admin=True)
        self.assertEqual(len(result["members"]), 61)
        self.assertTrue(result["members"][0]["subscribed"])
        self.assertFalse(result["members"][1]["subscribed"])
        self.assertNotIn("price_usd", result["members"][0])
        cursor.execute.assert_called_once()
        self.assertEqual(cursor.execute.call_args.args[1][:3], [99, 99, 99])

    def payload(self, **changes):
        return {"staff_id": 1, "request_id": "a" * 32, "revision": 0,
                "employment": "在职", "circumstance": "请假", "subscribed": False,
                "confirmed": True, **changes}

    def test_private_labels_are_fixed_management_categories(self):
        with patch.object(management, "_admin"):
            for changes in ({"circumstance": "health-reason"}, {"employment": "unknown"},
                            {"confirmed": False}, {"subscribed": "true"}, {"staff_id": True}):
                with self.assertRaises(ValueError):
                    management.save(self.payload(**changes), 1)

    def test_departed_staff_cannot_enable_subscription(self):
        with patch.object(management, "_admin"), patch.object(management, "get_conn") as connection:
            with self.assertRaises(ValueError):
                management.save(self.payload(employment="离职", subscribed=True), 1)
        connection.assert_not_called()

    def test_new_subscription_requires_explicit_authorization(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [{"id": 1, "ended_at": None}, None, None, None]
        with patch.object(management, "_admin"), patch.object(management, "get_conn", return_value=conn):
            with self.assertRaises(ValueError):
                management.save(self.payload(subscribed=True), 1)
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_replacement_does_not_transfer_subscription(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [{"id": 1, "ended_at": "2026-01-01"}, None]
        with patch.object(management, "_admin"), patch.object(management, "get_conn", return_value=conn):
            with self.assertRaises(RecordConflict):
                management.save(self.payload(subscribed=True, authorized=True), 1)
        conn.commit.assert_not_called()

    def test_admin_check_rejects_public_user(self):
        with patch.object(management, "admin_ids", return_value={1}):
            with self.assertRaises(ValueError):
                management._admin(2)
