# Registers "ASV Qdrant daily ingest" for the current user: 07:30 local time
# every day, catching up at next logon if the laptop was off. Re-running
# replaces the task. Remove with:
#   Unregister-ScheduledTask -TaskName "ASV Qdrant daily ingest" -Confirm:$false

param([string]$At = "07:30")

$script = Join-Path $PSScriptRoot "daily-update.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "ASV Qdrant daily ingest" -Action $action -Trigger $trigger `
    -Settings $settings -Description "Diff-ingest ASV engineering notes into Qdrant (asv-qdrant-index)" -Force |
    Select-Object TaskName, State
