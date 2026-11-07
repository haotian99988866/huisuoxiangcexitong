$ErrorActionPreference = 'Stop'

$repository = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $PSScriptRoot 'run_local_backup.ps1'
$python = Join-Path $repository 'bot\venv\Scripts\python.exe'
$names = @('HuisuoDailyLocalBackup', 'HuisuoWeeklyMusicBackup')

if (-not (Test-Path 'D:\' -PathType Container)) {
    throw 'The D: drive is not available.'
}
if (-not (Test-Path $python -PathType Leaf) -or -not (Test-Path $runner -PathType Leaf)) {
    throw 'The project Python or backup runner is missing.'
}
foreach ($name in $names) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        throw "Task $name already exists; inspect it before replacing anything."
    }
}

$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-ScheduledTaskPrincipal -UserId $identity.User.Value `
    -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 15)
$powershell = Join-Path $PSHOME 'powershell.exe'
$dailyAction = New-ScheduledTaskAction -Execute $powershell `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runner`"" `
    -WorkingDirectory $repository
$weeklyAction = New-ScheduledTaskAction -Execute $powershell `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runner`" -IncludeLocalMusic" `
    -WorkingDirectory $repository
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At '3:00 AM'
$weeklyTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '4:00 AM'

Register-ScheduledTask -TaskName $names[0] -Action $dailyAction `
    -Trigger $dailyTrigger -Principal $principal -Settings $settings `
    -Description 'Daily local database, state, and announcement-media backup to D:.' | Out-Null
Register-ScheduledTask -TaskName $names[1] -Action $weeklyAction `
    -Trigger $weeklyTrigger -Principal $principal -Settings $settings `
    -Description 'Weekly full local backup including the music library to D:.' | Out-Null

foreach ($name in $names) {
    $task = Get-ScheduledTask -TaskName $name
    $info = Get-ScheduledTaskInfo -TaskName $name
    [pscustomobject]@{
        Name = $name
        State = $task.State
        NextRunTime = $info.NextRunTime
        Action = ($task.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join '; '
    }
}
