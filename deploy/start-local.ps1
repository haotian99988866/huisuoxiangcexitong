# 本机 + Cloudflare 临时隧道：一键启动 API、机器人、前端预览和隧道，并打印公网地址。
# 用法：在项目根目录运行  powershell -ExecutionPolicy Bypass -File deploy\start-local.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root 'bot\venv\Scripts\python.exe'
$cf = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
$log = $env:TEMP
$env:PYTHONIOENCODING = 'utf-8'

function Stop-Matching($pattern) {
    Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -and $_.CommandLine -match $pattern -and $_.ProcessId -ne $PID } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

# 先停掉旧进程，避免重复运行（同一个机器人只能运行一个实例）
Stop-Matching 'api_server\.py'
Stop-Matching 'bot\\main\.py|\bmain\.py'
Stop-Matching 'vite.*preview'
Stop-Matching 'cloudflared.*tunnel'
Start-Sleep 2

Push-Location (Join-Path $root 'bot')
Start-Process $py -ArgumentList 'api_server.py' -WindowStyle Hidden `
    -RedirectStandardOutput "$log\api.out.log" -RedirectStandardError "$log\api.err.log"
Start-Process $py -ArgumentList 'main.py' -WindowStyle Hidden `
    -RedirectStandardOutput "$log\bot.out.log" -RedirectStandardError "$log\bot.err.log"
Pop-Location

Push-Location (Join-Path $root 'miniapp')
npm run build | Out-Null
Start-Process node -ArgumentList 'node_modules\vite\bin\vite.js', 'preview' -WindowStyle Hidden `
    -RedirectStandardOutput "$log\preview.out.log" -RedirectStandardError "$log\preview.err.log"
Pop-Location

Start-Sleep 4
Remove-Item "$log\tunnel.err.log" -ErrorAction SilentlyContinue
Start-Process $cf -ArgumentList 'tunnel', '--url', 'http://127.0.0.1:4173' -WindowStyle Hidden `
    -RedirectStandardOutput "$log\tunnel.out.log" -RedirectStandardError "$log\tunnel.err.log"

$url = $null
for ($i = 0; $i -lt 30 -and -not $url; $i++) {
    Start-Sleep 1
    if (Test-Path "$log\tunnel.err.log") {
        $m = Select-String -Path "$log\tunnel.err.log" -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1
        if ($m) { $url = $m.Matches[0].Value }
    }
}
if ($url) {
    Write-Host "公网地址：$url" -ForegroundColor Green
    Write-Host '每次启动地址都会变：到 BotFather 把小程序网址改成上面的地址。'
} else {
    Write-Host "未获取到公网地址，请查看 $log\tunnel.err.log" -ForegroundColor Red
}
