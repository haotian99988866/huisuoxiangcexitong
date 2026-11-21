# Start local services and the named tunnel; heal zombie listeners before skip.
# Run: powershell -ExecutionPolicy Bypass -File deploy\start-local.ps1
# channel_sync 由 deploy\start-channel-sync.ps1 / watchdog 单独拉起，本脚本不启动。
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root 'bot\venv\Scripts\python.exe'
$node = (Get-Command node -ErrorAction Stop).Source
$cf = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
$config = Join-Path $env:USERPROFILE '.cloudflared\config.yml'
$log = Join-Path $env:LOCALAPPDATA 'Huisuo\logs'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
if (-not $env:MEMBER_SITE_URL) {
    $env:MEMBER_SITE_URL = 'https://app.haotianzhushou.com/membership'
}

New-Item -ItemType Directory -Path $log -Force | Out-Null
Start-Transcript -Path (Join-Path $log 'startup.log') -Append | Out-Null
try {
    foreach ($path in @($py, $cf, $config, (Join-Path $root 'miniapp\dist\index.html'))) {
        if (-not (Test-Path -LiteralPath $path)) {
            throw "Required file missing: $path"
        }
    }

    function Test-HttpOk($url, $post) {
        try {
            $options = @{ Uri = $url; UseBasicParsing = $true; TimeoutSec = 8 }
            if ($post) {
                $options.Method = 'Post'
                $options.ContentType = 'application/json'
                $options.Body = '{"action":"get_venues"}'
            }
            $response = Invoke-WebRequest @options
            if ($response.StatusCode -ne 200) { return $false }
            if ($post -and -not (($response.Content | ConvertFrom-Json).venues)) { return $false }
            return $true
        } catch {
            return $false
        }
    }

    function Stop-PortListeners($port) {
        $pids = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique)
        foreach ($procId in $pids) {
            if (-not $procId) { continue }
            try {
                Stop-Process -Id $procId -Force -ErrorAction Stop
                Write-Host "Stopped stale listener PID $procId on port $port."
            } catch {
                Write-Warning "Could not stop PID ${procId}: $($_.Exception.Message)"
            }
        }
    }

    function Clear-ZombieEndpoint($url, $post, $port, $name) {
        if (Test-HttpOk $url $post) {
            Write-Host "$name endpoint healthy."
            return
        }
        Write-Warning "$name endpoint unhealthy; clearing port $port listeners."
        Stop-PortListeners $port
        Start-Sleep -Seconds 1
    }

    function Start-ServiceProcess($executable, $pattern, $arguments, $directory, $name) {
        $running = @(Get-CimInstance Win32_Process | Where-Object {
            $_.ExecutablePath -eq $executable -and $_.CommandLine -match $pattern
        })
        if ($running.Count -gt 0) {
            Write-Host "$name already running (PID $($running[0].ProcessId))."
            return
        }
        Start-Process -FilePath $executable -ArgumentList $arguments `
            -WorkingDirectory $directory -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $log "$name.out.log") `
            -RedirectStandardError (Join-Path $log "$name.err.log")
        Write-Host "$name started."
    }

    function Wait-Endpoint($url, $post) {
        for ($i = 0; $i -lt 18; $i++) {
            if (Test-HttpOk $url $post) {
                Write-Host "Ready: $url"
                return
            }
            if ($i -eq 17) { throw "Endpoint not ready: $url" }
            Write-Warning "Waiting for ${url}..."
            Start-Sleep -Seconds 5
        }
        throw "Endpoint not ready: $url"
    }

    function Assert-ProcessRunning($executable, $pattern, $name) {
        $running = @(Get-CimInstance Win32_Process | Where-Object {
            $_.ExecutablePath -eq $executable -and $_.CommandLine -match $pattern
        })
        if ($running.Count -eq 0) {
            throw "$name process not found after start."
        }
        Write-Host "$name process OK (PID $($running[0].ProcessId))."
    }

    # Heal fake-alive listeners before "already running" skip
    Clear-ZombieEndpoint 'http://127.0.0.1:8000/webhook' $true 8000 'api'
    Clear-ZombieEndpoint 'http://127.0.0.1:4173/' $false 4173 'preview'

    $botDirectory = Join-Path $root 'bot'
    $appDirectory = Join-Path $root 'miniapp'
    Start-ServiceProcess $py '\bapi_server\.py(?:["\s]|$)' 'api_server.py' $botDirectory 'api'
    Start-ServiceProcess $py '\bmain\.py(?:["\s]|$)' 'main.py' $botDirectory 'bot'
    $financeTokenInFile = Select-String -Path (Join-Path $botDirectory '.env') `
        -Pattern '^\s*FINANCE_BOT_TOKEN\s*=\s*\S+' -Quiet -ErrorAction SilentlyContinue
    $financeOwnerInFile = Select-String -Path (Join-Path $botDirectory '.env') `
        -Pattern '^\s*FINANCE_BOT_OWNER_ID\s*=\s*\d+' -Quiet -ErrorAction SilentlyContinue
    $financeTokenConfigured = [bool]$env:FINANCE_BOT_TOKEN -or [bool]$financeTokenInFile
    $financeOwnerConfigured = [bool]$env:FINANCE_BOT_OWNER_ID -or [bool]$financeOwnerInFile
    if ($financeTokenConfigured -and $financeOwnerConfigured) {
        Start-ServiceProcess $py '\bfinance_bot\.py(?:["\s]|$)' `
            'finance_bot.py' $botDirectory 'finance-bot'
    } else {
        Write-Host 'Personal finance bot is not configured; skipping it.'
    }
    Start-ServiceProcess $node 'vite[\\/].*preview|vite\.js"?\s+preview' `
        'node_modules\vite\bin\vite.js preview' $appDirectory 'preview'
    Wait-Endpoint 'http://127.0.0.1:4173/' $false
    Wait-Endpoint 'http://127.0.0.1:8000/webhook' $true
    Assert-ProcessRunning $py '\bmain\.py(?:["\s]|$)' 'bot'
    Start-ServiceProcess $cf 'tunnel\s+(?:--config\s+"[^"]+"\s+)?run\s+huisuo(?:\s|$)' `
        "tunnel --config `"$config`" run huisuo" $root 'tunnel'
    Wait-Endpoint 'https://app.haotianzhushou.com/' $false
    Wait-Endpoint 'https://app.haotianzhushou.com/api/webhook' $true
    Write-Host 'Startup complete: https://app.haotianzhushou.com/'
    Write-Host 'Note: channel_sync is started separately via deploy\start-channel-sync.ps1 or watchdog.'
} catch {
    Write-Error "Startup failed. Logs: $log. $($_.Exception.Message)"
    exit 1
} finally {
    Stop-Transcript | Out-Null
}
