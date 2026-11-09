import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy import watchdog


class WatchdogTests(unittest.TestCase):
    def test_inventory_includes_relative_preview_using_listening_process_id(self):
        row = {
            "ProcessId": 15752,
            "Name": "node.exe",
            "CommandLine": (
                r'"C:\Program Files\nodejs\node.exe" '
                r'node_modules\vite\bin\vite.js preview'
            ),
        }
        response = MagicMock(returncode=0, stdout=json.dumps(row))
        with patch.object(watchdog.subprocess, "run", return_value=response) as run:
            rows = watchdog.process_inventory()
        query = run.call_args.args[0][-1]
        self.assertIn("Get-NetTCPConnection -State Listen -ErrorAction Stop", query)
        self.assertIn("$_.LocalPort -eq 4173", query)
        self.assertIn("$previewPids -contains $_.ProcessId", query)
        self.assertIn("$_.Name -eq 'node.exe'", query)
        self.assertEqual(rows, [row])
        self.assertTrue(watchdog.classify_processes(rows)["preview"])

    def test_classifies_existing_service_processes(self):
        rows = [
            {"Name": "python.exe", "CommandLine": f'"{ROOT}\\bot\\api_server.py"'},
            {"Name": "python.exe", "CommandLine": f'"{ROOT}\\bot\\main.py"'},
            {"Name": "node.exe", "CommandLine": f'"{ROOT}\\miniapp\\node_modules\\vite\\bin\\vite.js" preview'},
            {"Name": "python.exe", "CommandLine": f'"{ROOT}\\bot\\channel_sync.py" run'},
            {"Name": "cloudflared.exe", "CommandLine": 'cloudflared tunnel --config "C:\\Users\\user\\.cloudflared\\config.yml" run huisuo'},
        ]
        self.assertEqual(set(watchdog.classify_processes(rows)), watchdog.EXPECTED_ROLES)
        self.assertTrue(all(watchdog.classify_processes(rows).values()))

    def test_detects_http_failures_without_treating_live_processes_as_missing(self):
        rows = [
            {"Name": "python.exe", "CommandLine": f'"{ROOT}\\bot\\api_server.py"'},
            {"Name": "python.exe", "CommandLine": f'"{ROOT}\\bot\\main.py"'},
            {"Name": "node.exe", "CommandLine": f'"{ROOT}\\miniapp\\vite.js" preview'},
            {"Name": "python.exe", "CommandLine": f'"{ROOT}\\bot\\channel_sync.py" run'},
            {"Name": "cloudflared.exe", "CommandLine": 'cloudflared tunnel run huisuo'},
        ]
        probes = {
            "api_http": False,
            "preview_http": True,
            "public_site": True,
            "public_api": True,
        }
        roles, _, issues = watchdog.collect_status(rows, probes)
        self.assertTrue(all(roles.values()))
        self.assertEqual(issues, {"api_http": "本机 API 检查失败"})
        with patch.object(watchdog, "run_recovery_script") as start:
            self.assertEqual(watchdog.recover_missing(roles), [])
        start.assert_not_called()

    def test_missing_service_roles_use_existing_start_scripts(self):
        roles = {role: True for role in watchdog.EXPECTED_ROLES}
        roles["api_server"] = False
        roles["channel_sync"] = False
        with patch.object(watchdog, "run_recovery_script", return_value=0) as start:
            attempts = watchdog.recover_missing(roles)
        self.assertEqual(attempts, ["主服务", "频道同步"])
        self.assertEqual(
            [call.args[0] for call in start.call_args_list],
            [watchdog.START_LOCAL, watchdog.START_CHANNEL_SYNC],
        )

    def test_incident_alerts_are_deduplicated_repeated_and_recovered(self):
        with tempfile.TemporaryDirectory() as directory:
            state = watchdog.IncidentState(Path(directory) / "state.json")
            issues = {"api_http": "本机 API 检查失败"}
            self.assertEqual(state.update(issues, 1000), [("issue", "api_http", issues["api_http"])])
            self.assertEqual(state.update(issues, 1001), [])
            repeat_at = 1000 + watchdog.REPEAT_ALERT_SECONDS
            self.assertEqual(state.update(issues, repeat_at), [("issue", "api_http", issues["api_http"])])
            self.assertEqual(state.update({}, repeat_at + 1), [("recovery", "api_http", issues["api_http"])])
            state.save()
            restored = watchdog.IncidentState(Path(directory) / "state.json")
            self.assertEqual(restored.active, {})
            self.assertEqual(
                restored.recovery_notice(["主服务"], repeat_at),
                ["主服务"],
            )
            self.assertEqual(restored.recovery_notice(["主服务"], repeat_at + 1), [])

    def test_dry_run_does_not_launch_services_send_or_persist_incidents(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(watchdog, "watchdog_directory", return_value=Path(directory)), \
                    patch.object(watchdog, "collect_status", return_value=(
                        {"api_server": False, "main_bot": True, "preview": True,
                         "cloudflared_tunnel": True, "channel_sync": True},
                        {},
                        {"api_server": "进程未运行"},
                    )), \
                    patch.object(watchdog, "run_recovery_script") as start, \
                    patch.object(watchdog, "send_admin_message") as send:
                result = watchdog.run_once(dry_run=True)
            self.assertIn("api_server", result["issues"])
            start.assert_not_called()
            send.assert_not_called()
            self.assertFalse((Path(directory) / "monitor-state.json").exists())

    def test_run_once_skips_duplicate_probes_when_no_recovery_is_needed(self):
        roles = {role: True for role in watchdog.EXPECTED_ROLES}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(watchdog, "watchdog_directory", return_value=Path(directory)), \
                patch.object(watchdog, "collect_status", return_value=(roles, {}, {})) as collect:
            result = watchdog.run_once()

        collect.assert_called_once_with()
        self.assertEqual(result["issues"], {})
        self.assertEqual(result["recovered"], [])

    def test_run_once_rechecks_services_after_recovery_attempts(self):
        roles = {role: True for role in watchdog.EXPECTED_ROLES}
        missing_api = dict(roles, api_server=False)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(watchdog, "watchdog_directory", return_value=Path(directory)), \
                patch.object(watchdog, "collect_status", side_effect=[
                    (missing_api, {}, {"api_server": "进程未运行"}),
                    (roles, {}, {}),
                ]) as collect, \
                patch.object(watchdog, "run_recovery_script", return_value=0) as recover, \
                patch.object(watchdog, "send_admin_message"):
            result = watchdog.run_once()

        self.assertEqual(collect.call_count, 2)
        recover.assert_called_once_with(watchdog.START_LOCAL, 480)
        self.assertEqual(result["issues"], {})
        self.assertEqual(result["recovered"], ["主服务"])

    def test_direct_telegram_alert_uses_database_owner_without_starting_a_bot(self):
        response = MagicMock()
        response.json.return_value = {"ok": True}
        with patch.object(watchdog, "load_dotenv"), \
                patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test-token"}), \
                patch("deploy.watchdog.import_module") as import_module, \
                patch.object(watchdog.requests, "post", return_value=response) as post:
            import_module.return_value.manual_owner.return_value = 456
            watchdog.send_admin_message("test alert")
        import_module.assert_called_once_with("premium_balance")
        response.raise_for_status.assert_called_once_with()
        self.assertEqual(
            post.call_args.args[0],
            "https://api.telegram.org/bottest-token/sendMessage",
        )
        self.assertEqual(post.call_args.kwargs["data"], {"chat_id": 456, "text": "test alert"})


if __name__ == "__main__":
    unittest.main()
