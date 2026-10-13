$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$python = 'D:\workspace\conda-envs\deepstock\python.exe'
if (-not (Test-Path $python)) { throw 'Dedicated Deepstock interpreter unavailable' }
$taskName = 'Deepstock_ADX_Shadow'
$backupFolder = Join-Path $project 'artifacts\research\adx-shadow-v1\scheduler-backups'
New-Item -ItemType Directory -Force -Path $backupFolder | Out-Null
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Export-ScheduledTask -TaskName $taskName | Set-Content -Encoding UTF8 -Path (Join-Path $backupFolder ((Get-Date -Format 'yyyyMMddHHmmss') + '.xml'))
}
$action = New-ScheduledTaskAction -Execute $python -Argument '-m scripts.run_scheduled_task adx-shadow' -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Daily -At '07:45'
$options = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$principal = New-ScheduledTaskPrincipal -UserId 'admin' -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $options -Principal $principal -Force | Select-Object TaskName, State
