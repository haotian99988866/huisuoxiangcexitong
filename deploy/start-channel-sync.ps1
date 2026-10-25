$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root 'bot\venv\Scripts\python.exe'
$script = Join-Path $root 'bot\channel_sync.py'
$directory = Join-Path $env:LOCALAPPDATA 'Huisuo\channel-sync'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
New-Item -ItemType Directory -Path $directory -Force | Out-Null
$startupLog = Join-Path $directory 'startup.log'
$mutex = New-Object System.Threading.Mutex($false, 'Local\HuisuoChannelSyncStartup')
$locked = $false
try {
    $locked = $mutex.WaitOne(0)
    if (-not $locked) { throw 'Another channel-sync startup command is running.' }
    foreach ($path in @($python, $script, (Join-Path $root 'bot\.channel-sync.env'), (Join-Path $directory 'user.session'))) {
        if (-not (Test-Path -LiteralPath $path)) { throw "Required file missing: $path" }
    }
    $running = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^python(w)?\.exe$' -and
        $_.CommandLine -match 'channel_sync\.py"?\s+run(?:\s|$)' -and
        $_.CommandLine -like "*$root*"
    }
    if ($running) {
        "$(Get-Date -Format o) Already running; no duplicate started." | Add-Content -LiteralPath $startupLog
        exit 0
    }
    $log = Join-Path $directory 'sync.log'
    $offset = 0
    if (Test-Path -LiteralPath $log) { $offset = (Get-Item -LiteralPath $log).Length }
    $process = Start-Process -FilePath $python -ArgumentList "-u `"$script`" run" `
        -WorkingDirectory $root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $directory 'listener.out.log') `
        -RedirectStandardError (Join-Path $directory 'listener.err.log')
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Seconds 2
        $process.Refresh()
        if ($process.HasExited) { throw "Listener exited with code $($process.ExitCode). See listener.err.log." }
        if (Test-Path -LiteralPath $log) {
            $stream = [System.IO.File]::Open($log, 'Open', 'Read', 'ReadWrite')
            try {
                [void]$stream.Seek($offset, 'Begin')
                $reader = New-Object System.IO.StreamReader($stream)
                try { $newLog = $reader.ReadToEnd() } finally { $reader.Dispose() }
            } finally { $stream.Dispose() }
            if ($newLog -match 'INFO READY: @svip -> @haotiancm99988866;') {
                "$(Get-Date -Format o) READY; launcher PID $($process.Id)." | Add-Content -LiteralPath $startupLog
                exit 0
            }
        }
    }
    throw "Listener is still running but readiness was not confirmed within 120 seconds. Inspect sync.log; do not start another instance."
} catch {
    "$(Get-Date -Format o) ERROR: $($_.Exception.Message)" | Add-Content -LiteralPath $startupLog
    Write-Error $_
    exit 1
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
