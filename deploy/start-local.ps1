# Start local services and the named tunnel; leave existing instances running.
# Run: powershell -ExecutionPolicy Bypass -File deploy\start-local.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root 'bot\venv\Scripts\python.exe'
$node = (Get-Command node -ErrorAction Stop).Source
$cf = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
$config = Join-Path $env:USERPROFILE '.cloudflared\config.yml'
$log = Join-Path $env:LOCALAPPDATA 'Huisuo\logs'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

New-Item -ItemType Directory -Path $log -Force | Out-Null
Start-Transcript -Path (Join-Path $log 'startup.log') -Append | Out-Null
try {
    foreach ($path in @($py, $cf, $config, (Join-Path $root 'miniapp\dist\index.html'))) {
        if (-not (Test-Path -LiteralPath $path)) {
            throw "Required file missing: $path"
        }
    }

    function Start-ServiceProcess($executable, $pattern, $arguments, $directory, $name) {
        $running = Get-CimInstance Win32_Process | Where-Object {
            $_.ExecutablePath -eq $executable -and $_.CommandLine -match $pattern
        }
        if ($running) {
            Write-Host "$name already running."
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
            try {
                $options = @{ Uri = $url; UseBasicParsing = $true; TimeoutSec = 10 }
                if ($post) {
                    $options.Method = 'Post'
                    $options.ContentType = 'application/json'
                    $options.Body = '{"action":"get_venues"}'
                }
                $response = Invoke-WebRequest @options
                if ($response.StatusCode -eq 200) {
                    if ($post -and -not (($response.Content | ConvertFrom-Json).venues)) {
                        throw 'Venue response is empty or invalid.'
                    }
                    Write-Host "Ready: $url"
                    return
                }
            } catch {
                if ($i -eq 17) { throw }
                Write-Warning "Waiting for ${url}: $($_.Exception.Message)"
            }
            Start-Sleep -Seconds 5
        }
        throw "Endpoint not ready: $url"
    }

    $botDirectory = Join-Path $root 'bot'
    $appDirectory = Join-Path $root 'miniapp'
    Start-ServiceProcess $py '\bapi_server\.py(?:["\s]|$)' 'api_server.py' $botDirectory 'api'
    Start-ServiceProcess $py '\bmain\.py(?:["\s]|$)' 'main.py' $botDirectory 'bot'
    Start-ServiceProcess $node 'vite[\\/].*preview|vite\.js"?\s+preview' `
        'node_modules\vite\bin\vite.js preview' $appDirectory 'preview'
    Wait-Endpoint 'http://127.0.0.1:4173/' $false
    Wait-Endpoint 'http://127.0.0.1:8000/webhook' $true
    Start-ServiceProcess $cf 'tunnel\s+(?:--config\s+"[^"]+"\s+)?run\s+huisuo(?:\s|$)' `
        "tunnel --config `"$config`" run huisuo" $root 'tunnel'
    Wait-Endpoint 'https://app.haotianzhushou.com/' $false
    Wait-Endpoint 'https://app.haotianzhushou.com/api/webhook' $true
    Write-Host 'Startup complete: https://app.haotianzhushou.com/'
} catch {
    Write-Error "Startup failed. Logs: $log. $($_.Exception.Message)"
    exit 1
} finally {
    Stop-Transcript | Out-Null
}
