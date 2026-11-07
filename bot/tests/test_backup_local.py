import importlib.util
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


BACKUP_SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "backup_local.py"
SPEC = importlib.util.spec_from_file_location("backup_local", BACKUP_SCRIPT)
backup_local = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup_local)


class LocalBackupTests(unittest.TestCase):
    def test_backup_root_can_be_selected_without_changing_other_local_paths(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
                "os.environ", {"LOCALAPPDATA": temporary}):
            root = Path(temporary) / "external-drive" / "backups"
            paths = backup_local.local_paths(root)

            self.assertEqual(paths["backup_root"], root)
            self.assertEqual(paths["state"], Path(temporary) / "Huisuo" / "channel-sync")

    def test_backup_directory_acl_uses_the_current_windows_sid(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(backup_local.subprocess, "check_output",
                             return_value="S-1-5-21-1234") as current_sid, \
                patch.object(backup_local.subprocess, "run",
                             return_value=type("Result", (), {"returncode": 0})()) as icacls:
            path = Path(temporary) / "backups"
            backup_local.protect_directory(path)

        current_sid.assert_called_once_with(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                "[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value",
            ],
            text=True,
            encoding="ascii",
        )
        self.assertIn("*S-1-5-21-1234:(OI)(CI)F", icacls.call_args.args[0])

    def test_sqlite_snapshot_is_consistent_and_readable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "state"
            source.mkdir()
            with closing(sqlite3.connect(source / "jobs.sqlite3")) as database:
                with database:
                    database.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, status TEXT)")
                    database.executemany("INSERT INTO jobs(status) VALUES (?)", [("queued",), ("sent",)])

            records = backup_local.sqlite_snapshots(source, root / "package")

            self.assertIn("state/jobs.sqlite3", records)
            with closing(sqlite3.connect(root / "package" / "jobs.sqlite3")) as restored:
                self.assertEqual(restored.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(restored.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 2)

    def test_manifest_verifies_exact_file_inventory_and_checksum(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            payload = package / "state" / "jobs.sqlite3"
            payload.parent.mkdir()
            payload.write_bytes(b"state")
            manifest = {
                "files": {
                    "state/jobs.sqlite3": {
                        "bytes": payload.stat().st_size,
                        "sha256": backup_local.sha256_file(payload),
                    }
                }
            }

            self.assertEqual(set(backup_local.checked_files(package, manifest)), {"state/jobs.sqlite3"})
            payload.write_bytes(b"State")
            with self.assertRaisesRegex(RuntimeError, "checksum failed"):
                backup_local.checked_files(package, manifest)

    def test_manifest_rejects_unlisted_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            payload = package / "state" / "jobs.sqlite3"
            payload.parent.mkdir()
            payload.write_bytes(b"state")
            manifest = {
                "files": {
                    "state/jobs.sqlite3": {
                        "bytes": payload.stat().st_size,
                        "sha256": backup_local.sha256_file(payload),
                    }
                }
            }
            (package / "unexpected.txt").write_text("extra", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "inventory"):
                backup_local.checked_files(package, manifest)

    def test_manifest_rejects_paths_outside_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            manifest = {"files": {"../outside.sql": {"bytes": 1, "sha256": "bad"}}}
            with self.assertRaisesRegex(RuntimeError, "inventory"):
                backup_local.checked_files(package, manifest)

    def test_retention_removes_only_expired_backup_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "20200101T000000Z"
            recent = root / "20990101T000000Z"
            unrelated = root / "keep-this"
            partial = root / "20200101T000000Z.partial"
            for path in (old, recent, unrelated, partial):
                path.mkdir()

            backup_local.prune_backups(root, 14)

            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(unrelated.exists())
            self.assertTrue(partial.exists())


if __name__ == "__main__":
    unittest.main()
