"""User-session watchdog for the local Huisuo Windows deployment."""
import argparse
from importlib import import_module
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import time

import requests
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot"
START_LOCAL = ROOT / "deploy" / "start-local.ps1"
START_CHANNEL_SYNC = ROOT / "deploy" / "start-channel-sync.ps1"
PYTHON = ROOT / "bot" / "venv" / "Scripts" / "python.exe"
TASK_NAME = "HuisuoLocalWatchdog"
REPEAT_ALERT_SECONDS = 30 * 60
CORE_ROLES = {"api_server", "main_bot", "preview", "cloudflared_tunnel"}
EXPECTED_ROLES = CORE_ROLES | {"channel_sync"}


def watchdog_directory():
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("LOCALAPPDATA is not configured; watchdog requires a Windows user session")
    return Path(local) / "Huisuo" / "watchdog"


def prepare_directory(path):
    path.mkdir(parents=True, exist_ok=True)
    identity_sid = subprocess.check_output(
        [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value",
        ],
        text=True,
        encoding="ascii",
    ).strip()
    result = subprocess.run(
        [
            "icacls.exe", str(path), "/inheritance:r", "/grant:r",
            f"*{identity_sid}:(OI)(CI)F", "NT AUTHORITY\\SYSTEM:(OI)(CI)F",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Could not restrict watchdog state permissions")


def configure_logging(directory):
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("huisuo.watchdog")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(
            directory / "watchdog.log", maxBytes=2 * 1024 * 1024,
            backupCount=4, encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


def process_inventory():
    escaped_root = str(ROOT).replace("'", "''")
    query = (
        "$root='" + escaped_root + "';"
        "$previewPids=@(Get-NetTCPConnection -State Listen -ErrorAction Stop | "
        "Where-Object { $_.LocalPort -eq 4173 } | "
        "Select-Object -ExpandProperty OwningProcess);"
        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and "
        "((($_.CommandLine.Contains($root)) -and "
        "($_.Name -match '^(python|pythonw|node)\\.exe$')) -or "
        "(($_.Name -eq 'node.exe') -and "
        "($previewPids -contains $_.ProcessId) -and "
        "($_.CommandLine -match '(?i)vite[\\\\/].*preview|vite\\.js\"?\\s+preview')) -or "
        "(($_.Name -eq 'cloudflared.exe') -and "
        "($_.CommandLine -match '(?i)\\btunnel\\b.*\\brun\\b.*\\bhuisuo\\b'))) } | "
        "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", query],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Could not read Windows process inventory")
    if not result.stdout.strip():
        return []
    rows = json.loads(result.stdout)
    if isinstance(rows, dict):
        rows = [rows]
    return rows


def classify_processes(rows):
    found = {role: False for role in EXPECTED_ROLES}
    for row in rows:
        name = row.get("Name", "").casefold()
        command = row.get("CommandLine", "")
        folded = command.casefold()
        if "api_server.py" in folded:
            found["api_server"] = True
        if "main.py" in folded:
            found["main_bot"] = True
        if "channel_sync.py" in folded and " run" in folded:
            found["channel_sync"] = True
        if name in {"node.exe", "python.exe", "pythonw.exe"} and "preview" in folded:
            found["preview"] = True
        if name == "cloudflared.exe" and all(
                marker in folded for marker in ("tunnel", " run ", "huisuo")):
            found["cloudflared_tunnel"] = True
    return found


def probe_services():
    checks = {}
    try:
        response = requests.get("http://127.0.0.1:4173/", timeout=8)
        checks["preview_http"] = response.ok
    except requests.RequestException:
        checks["preview_http"] = False
    try:
        response = requests.post(
            "http://127.0.0.1:8000/webhook",
            json={"action": "get_venues"},
            timeout=8,
        )
        payload = response.json()
        checks["api_http"] = response.ok and isinstance(payload.get("venues"), list)
    except (requests.RequestException, ValueError, AttributeError):
        checks["api_http"] = False
    try:
        checks["public_site"] = requests.get(
            "https://app.haotianzhushou.com/", timeout=12,
        ).ok
    except requests.RequestException:
        checks["public_site"] = False
    try:
        response = requests.post(
            "https://app.haotianzhushou.com/api/webhook",
            json={"action": "get_venues"},
            timeout=12,
        )
        payload = response.json()
        checks["public_api"] = response.ok and isinstance(payload.get("venues"), list)
    except (requests.RequestException, ValueError, AttributeError):
        checks["public_api"] = False
    return checks


def collect_status(processes=None, probes=None):
    roles = classify_processes(process_inventory() if processes is None else processes)
    probes = probe_services() if probes is None else probes
    issues = {
        role: "进程未运行"
        for role, running in roles.items()
        if not running
    }
    labels = {
        "api_http": "本机 API 检查失败",
        "preview_http": "本机前端检查失败",
        "public_site": "公网小程序不可访问",
        "public_api": "公网 API 检查失败",
    }
    issues.update({
        key: labels[key]
        for key, healthy in probes.items()
        if not healthy
    })
    return roles, probes, issues


def run_recovery_script(path, timeout):
    result = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(path),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return result.returncode


def recover_missing(roles, dry_run=False, logger=None):
    recovered_attempts = []
    if CORE_ROLES.difference(role for role, running in roles.items() if running):
        recovered_attempts.append("主服务")
        if not dry_run:
            code = run_recovery_script(START_LOCAL, 480)
            if code and logger:
                logger.error("Main service startup script failed with exit code %s", code)
    if not roles.get("channel_sync"):
        recovered_attempts.append("频道同步")
        if not dry_run:
            code = run_recovery_script(START_CHANNEL_SYNC, 150)
            if code and logger:
                logger.error("Channel sync startup script failed with exit code %s", code)
    return recovered_attempts


class IncidentState:
    def __init__(self, path):
        self.path = path
        self.active = {}
        self.recovery_notice_at = {}
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            self.active = state.get("active", {})
            self.recovery_notice_at = state.get("recovery_notice_at", {})
            if not isinstance(self.active, dict) or not isinstance(self.recovery_notice_at, dict):
                raise ValueError("Invalid watchdog state")
        except FileNotFoundError:
            pass

    def update(self, issues, now):
        notices = []
        next_active = {}
        for key, reason in issues.items():
            previous = self.active.get(key)
            if not previous or now - previous.get("last_notice_at", 0) >= REPEAT_ALERT_SECONDS:
                notices.append(("issue", key, reason))
                last_notice = now
            else:
                last_notice = previous["last_notice_at"]
            next_active[key] = {"reason": reason, "last_notice_at": last_notice}
        for key, previous in self.active.items():
            if key not in issues:
                notices.append(("recovery", key, previous.get("reason", "故障")))
        self.active = next_active
        return notices

    def recovery_notice(self, recovered, now):
        due = []
        for item in recovered:
            if now - self.recovery_notice_at.get(item, 0) >= REPEAT_ALERT_SECONDS:
                self.recovery_notice_at[item] = now
                due.append(item)
        return due

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({
                "active": self.active,
                "recovery_notice_at": self.recovery_notice_at,
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


def send_admin_message(text):
    root = ROOT
    load_dotenv(root / "bot" / ".env", override=True)
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Telegram bot token is not configured")
    sys.path.insert(0, str(BOT))
    manual_owner = import_module("premium_balance").manual_owner

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": manual_owner(), "text": text},
        timeout=15,
    )
    response.raise_for_status()
    if not response.json().get("ok"):
        raise RuntimeError("Telegram Bot API rejected the watchdog notification")


def notification_text(notices, recovered):
    lines = ["本机服务监测"]
    for kind, key, reason in notices:
        label = "已恢复" if kind == "recovery" else "异常"
        lines.append(f"• {label}：{key} — {reason}")
    if recovered:
        lines.append("• 自动拉起尝试：" + "、".join(recovered))
    return "\n".join(lines)


def run_once(dry_run=False, logger=None):
    logger = logger or logging.getLogger("huisuo.watchdog")
    state = IncidentState(watchdog_directory() / "monitor-state.json")
    roles, probes, issues = collect_status()
    recovered = recover_missing(roles, dry_run=dry_run, logger=logger)
    if recovered and not dry_run:
        roles, probes, issues = collect_status()
    now = time.time()
    if dry_run:
        logger.info("Dry-run status: %s", "healthy" if not issues else ", ".join(sorted(issues)))
        return {"roles": roles, "probes": probes, "issues": issues, "recovered": recovered}
    notices = state.update(issues, now)
    recovery_notice = state.recovery_notice(recovered, now) if not issues else []
    state.save()
    if issues:
        logger.warning("Watchdog issues: %s", ", ".join(sorted(issues)))
    elif recovered:
        logger.info("Watchdog recovery attempts completed; all service checks pass")
    else:
        logger.info("Watchdog checks passed: %s", ", ".join(sorted(EXPECTED_ROLES)))
    if notices or recovery_notice:
        recovered_for_message = recovered if recovery_notice else []
        text = notification_text(notices, recovered_for_message)
        try:
            send_admin_message(text)
        except Exception as error:
            logger.error("Independent Telegram alert failed: %s", type(error).__name__)
        else:
            logger.info("Independent Telegram alert sent")
    return {"roles": roles, "probes": probes, "issues": issues, "recovered": recovered}


def register_task():
    if not PYTHON.is_file():
        raise RuntimeError(f"Project virtual-environment Python not found: {PYTHON}")
    script = Path(__file__).resolve()
    powershell = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    ps = r"""
$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName 'HuisuoLocalWatchdog' -ErrorAction SilentlyContinue
if ($task) { throw 'Watchdog task already exists; refusing to replace it.' }
$account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$userXml = [System.Security.SecurityElement]::Escape($account)
$pythonXml = [System.Security.SecurityElement]::Escape($env:HUISUO_WATCHDOG_PYTHON)
$rootXml = [System.Security.SecurityElement]::Escape($env:HUISUO_WATCHDOG_ROOT)
$argumentsXml = [System.Security.SecurityElement]::Escape(('"' + $env:HUISUO_WATCHDOG_SCRIPT + '" --once'))
$startBoundary = (Get-Date).ToString('s')
$xml = @'
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Monitors and recovers Huisuo local services while the user is logged in.</Description>
  </RegistrationInfo>
  <Triggers>
    <RegistrationTrigger>
      <StartBoundary>__START_BOUNDARY__</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT5M</Interval>
      </Repetition>
      <Delay>PT5M</Delay>
    </RegistrationTrigger>
    <LogonTrigger>
      <StartBoundary>__START_BOUNDARY__</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT5M</Interval>
      </Repetition>
      <UserId>__USER__</UserId>
      <Delay>PT5M</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>__USER__</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT15M</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>__PYTHON__</Command>
      <Arguments>__ARGUMENTS__</Arguments>
      <WorkingDirectory>__ROOT__</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
'@
$xml = $xml.Replace('__START_BOUNDARY__', $startBoundary)
$xml = $xml.Replace('__USER__', $userXml)
$xml = $xml.Replace('__PYTHON__', $pythonXml)
$xml = $xml.Replace('__ARGUMENTS__', $argumentsXml)
$xml = $xml.Replace('__ROOT__', $rootXml)
Register-ScheduledTask -TaskName 'HuisuoLocalWatchdog' -Xml $xml | Out-Null
"""
    environment = os.environ.copy()
    environment.update({
        "HUISUO_WATCHDOG_PYTHON": str(PYTHON),
        "HUISUO_WATCHDOG_SCRIPT": str(script),
        "HUISUO_WATCHDOG_ROOT": str(ROOT),
    })
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Could not register the watchdog task: "
                           + (result.stderr or result.stdout)[-1500:])
    print(f"已注册当前用户计划任务：{TASK_NAME}")
    print("本次注册或用户登录后延迟 5 分钟启动，此后每 5 分钟检查一次。")


def main():
    parser = argparse.ArgumentParser(description="Huisuo local service watchdog")
    parser.add_argument("--once", action="store_true", help="Run one monitoring cycle")
    parser.add_argument("--dry-run", action="store_true", help="Do not launch or message")
    parser.add_argument("--register-task", action="store_true", help="Register and start the approved user watchdog task")
    args = parser.parse_args()
    directory = watchdog_directory()
    if args.register_task:
        prepare_directory(directory)
        register_task()
        return 0
    logger = configure_logging(directory)
    if args.once:
        result = run_once(dry_run=args.dry_run, logger=logger)
        print("当前状态：" + ("正常" if not result["issues"] else
                            "；".join(f"{key}：{value}" for key, value in result["issues"].items())))
        return 1 if result["issues"] else 0
    parser.error("Choose --once or --register-task")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"Watchdog failed: {error}", file=sys.stderr)
        raise SystemExit(1)
