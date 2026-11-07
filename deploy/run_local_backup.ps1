param(
    [switch]$IncludeLocalMusic
)

$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repository 'bot\venv\Scripts\python.exe'
$backupScript = Join-Path $PSScriptRoot 'backup_local.py'
$backupRoot = 'D:\Huisuo\backups'
$logDirectory = Join-Path $backupRoot 'logs'

if (-not (Test-Path $python -PathType Leaf)) {
    throw "Project Python was not found: $python"
}
New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$acl = & icacls.exe $backupRoot /inheritance:r /grant:r `
    ("*{0}:(OI)(CI)F" -f $sid) 'NT AUTHORITY\SYSTEM:(OI)(CI)F'
if ($LASTEXITCODE -ne 0) {
    throw "Could not restrict backup directory permissions: $($acl -join ' ')"
}
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$acl = & icacls.exe $logDirectory /inheritance:r /grant:r `
    ("*{0}:(OI)(CI)F" -f $sid) 'NT AUTHORITY\SYSTEM:(OI)(CI)F'
if ($LASTEXITCODE -ne 0) {
    throw "Could not restrict backup log permissions: $($acl -join ' ')"
}

$kind = if ($IncludeLocalMusic) { 'weekly-music' } else { 'daily' }
$logPath = Join-Path $logDirectory "$kind.log"
$outputPath = Join-Path $logDirectory "$kind-output.log"
$errorPath = Join-Path $logDirectory "$kind-error.log"
$arguments = "`"$backupScript`" backup --keep-days 14 --backup-root `"$backupRoot`" --verify-after-backup"
if ($IncludeLocalMusic) {
    $arguments += ' --include-local-music'
}

Push-Location $repository
try {
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')] Starting $kind backup." |
        Out-File -FilePath $logPath -Append -Encoding ascii
    $env:PYTHONIOENCODING = 'utf-8'
    $process = Start-Process -FilePath $python -ArgumentList $arguments `
        -WorkingDirectory $repository -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $outputPath -RedirectStandardError $errorPath
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')] Backup process exited with code $($process.ExitCode)." |
        Out-File -FilePath $logPath -Append -Encoding ascii
    exit $process.ExitCode
}
finally {
    Remove-Item Env:\PYTHONIOENCODING -ErrorAction SilentlyContinue
    Pop-Location
}
