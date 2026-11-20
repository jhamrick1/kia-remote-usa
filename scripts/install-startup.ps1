# Registers the "kia-remote" scheduled task: runs scripts\start.ps1 hidden at logon, for this user.
# No admin needed. Remove with:  Unregister-ScheduledTask -TaskName kia-remote -Confirm:$false
$start = Join-Path $PSScriptRoot 'start.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$start`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'kia-remote' -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Kia remote: car service + Cloudflare tunnel' -Force | Out-Null
Write-Output 'Scheduled task kia-remote registered (starts at logon).'
