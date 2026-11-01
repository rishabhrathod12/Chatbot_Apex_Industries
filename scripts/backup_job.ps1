<#
.SYNOPSIS
  Take a backup, copy it to one or more other places, and check every copy. Made to be run by the
  Windows Task Scheduler (see scripts\register_backup_task.ps1), but it can be run by hand.

.EXAMPLE
  .\scripts\backup_job.ps1 -Destination F:\ApexBackups
  .\scripts\backup_job.ps1 -Destination "F:\ApexBackups;\\nas\share\apex" -Keep 60

.NOTES
  - Nothing is deleted unless you pass -Keep N (then only the OLDEST backups beyond N are removed,
    in the local folder and in each destination). The default keeps everything.
  - Each copy is checked against the checksums in manifest.json. A copy that does not match makes
    the job fail (exit code 1) and is logged.
  - The log is backups\backup.log. It holds times, folder names and results, never passwords.
  - A destination on the same physical disk as the data protects against deleting a file by mistake,
    not against the disk failing. Use a USB drive, a network share or cloud storage for that.
#>
param(
    # One or more folders, separated by semicolons (a list would not survive the Task Scheduler).
    [Parameter(Mandatory = $true)] [string] $Destination,
    [int] $Keep = 0
)

$Places = @($Destination -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ })

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$BackupDir = Join-Path $Root 'backups'
$LogFile = Join-Path $BackupDir 'backup.log'

function Write-Log([string] $Message) {
    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -Path $LogFile -Value $line -Encoding utf8
    Write-Host $line
}

function Test-BackupCopy([string] $Folder) {
    <# Returns a list of problems (empty = every file is present and matches its checksum). #>
    $problems = @()
    $manifestPath = Join-Path $Folder 'manifest.json'
    if (-not (Test-Path $manifestPath)) { return @("manifest.json is missing in $Folder") }
    $manifest = Get-Content $manifestPath -Raw -Encoding utf8 | ConvertFrom-Json
    foreach ($entry in $manifest.files.PSObject.Properties) {
        $path = Join-Path $Folder $entry.Name
        if (-not (Test-Path $path)) { $problems += "$($entry.Name) is missing"; continue }
        $actual = (Get-FileHash -Path $path -Algorithm SHA256).Hash.ToLower()
        if ($actual -ne $entry.Value.sha256) { $problems += "$($entry.Name) does not match its checksum" }
    }
    return $problems
}

function Remove-OldBackups([string] $Folder, [int] $KeepCount) {
    if ($KeepCount -le 0 -or -not (Test-Path $Folder)) { return }
    $old = Get-ChildItem -Path $Folder -Directory -Filter 'apex-*' | Sort-Object Name -Descending | Select-Object -Skip $KeepCount
    foreach ($dir in $old) {
        Remove-Item -Path $dir.FullName -Recurse -Force
        Write-Log "removed old backup $($dir.Name) from $Folder (-Keep $KeepCount)"
    }
}

function Invoke-BackupJob {
    Write-Log "backup job started"
    $outFile = [IO.Path]::GetTempFileName(); $errFile = [IO.Path]::GetTempFileName()
    try {
        $process = Start-Process -FilePath $Python -ArgumentList @('scripts\backup.py', 'backup') `
            -WorkingDirectory $Root -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $outFile -RedirectStandardError $errFile
        if ($process.ExitCode -ne 0) {
            $why = (Get-Content $errFile -Raw -Encoding utf8)
            if (-not $why) { $why = (Get-Content $outFile -Raw -Encoding utf8) }
            throw "backup.py failed (exit $($process.ExitCode)): $($why.Trim())"
        }
    }
    finally { Remove-Item $outFile, $errFile -ErrorAction SilentlyContinue }

    $newest = Get-ChildItem -Path $BackupDir -Directory -Filter 'apex-*' | Sort-Object Name -Descending | Select-Object -First 1
    if (-not $newest) { throw "no backup folder was created in $BackupDir" }
    $local = Test-BackupCopy $newest.FullName
    if ($local) { throw "the new local backup $($newest.Name) is damaged: $($local -join '; ')" }
    Write-Log "local backup $($newest.Name) written and verified"

    $failed = 0
    foreach ($place in $Places) {
        try {
            New-Item -ItemType Directory -Force -Path $place | Out-Null
            $target = Join-Path $place $newest.Name
            Copy-Item -Path $newest.FullName -Destination $target -Recurse -Force
            $problems = Test-BackupCopy $target
            if ($problems) { throw ($problems -join '; ') }
            Write-Log "copied $($newest.Name) to $place and verified every file"
            Remove-OldBackups $place $Keep
        }
        catch {
            $failed++
            Write-Log "FAILED to copy to ${place}: $($_.Exception.Message)"
        }
    }
    Remove-OldBackups $BackupDir $Keep
    if ($failed -gt 0) { throw "$failed of $($Places.Count) destination(s) failed" }
    Write-Log "backup job finished: OK"
}

# Lets the checks above be loaded without running a backup:  . .\scripts\backup_job.ps1 -Destination x
if ($MyInvocation.InvocationName -ne '.') {
    try { Invoke-BackupJob; exit 0 }
    catch { Write-Log "backup job FAILED: $($_.Exception.Message)"; exit 1 }
}
