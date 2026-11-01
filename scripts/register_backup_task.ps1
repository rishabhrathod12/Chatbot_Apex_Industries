<#
.SYNOPSIS
  Register (or remove) a daily Windows Task Scheduler job that runs scripts\backup_job.ps1.

.EXAMPLE
  .\scripts\register_backup_task.ps1 -Destination "F:\ApexBackups" -DryRun      # show what would be set up
  .\scripts\register_backup_task.ps1 -Destination "E:\USB\ApexBackups" -Time 02:30
  .\scripts\register_backup_task.ps1 -Remove

.NOTES
  - The task runs as YOU, only while you are signed in, so no password is stored anywhere.
    If the computer is off or you are signed out at the scheduled time, it runs at your next sign-in
    (StartWhenAvailable). It is not a service.
  - Several destinations: separate them with semicolons inside the quotes.
  - -Keep N removes the oldest backups beyond N (locally and in each destination). 0 keeps everything.
  - Check it worked: Get-ScheduledTaskInfo -TaskName ApexBackup, and read backups\backup.log.
#>
param(
    [string] $Destination,
    [string] $Time = '02:00',
    [int] $Keep = 0,
    [string] $TaskName = 'ApexBackup',
    [switch] $DryRun,
    [switch] $Remove
)

$ErrorActionPreference = 'Stop'
$Job = Join-Path $PSScriptRoot 'backup_job.ps1'

if ($Remove) {
    if ($DryRun) { "Would remove the scheduled task '$TaskName'."; return }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    "Removed the scheduled task '$TaskName'. Existing backups were not touched."
    return
}

if (-not $Destination) { throw "Give -Destination (a folder, or several separated by semicolons)." }
if ($Time -notmatch '^([01]\d|2[0-3]):[0-5]\d$') { throw "-Time must look like 02:00 (24-hour)." }

$arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Job`" -Destination `"$Destination`" -Keep $Keep"
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited

if ($DryRun) {
    "DRY RUN: nothing was registered. The task '$TaskName' would:"
    "  run daily at $Time, as $($principal.UserId), only while signed in (no stored password)"
    "  command: powershell.exe $arguments"
    "  catch up at the next sign-in if the time was missed; stop after 1 hour; never run two at once"
    "  keep: " + $(if ($Keep -gt 0) { "the newest $Keep backups (older ones are deleted)" } else { "everything (nothing is deleted)" })
    return
}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description 'Nightly Apex backup: database, uploads, vector index; copies are verified.' -Force | Out-Null
"Registered '$TaskName': daily at $Time. Test it now with:  Start-ScheduledTask -TaskName $TaskName"
