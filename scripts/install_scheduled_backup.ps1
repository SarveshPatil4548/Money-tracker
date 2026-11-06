<#
    Installs (or removes) the Windows Task Scheduler job that backs up the
    Money Tracker database every day.

    Install:
        powershell -ExecutionPolicy Bypass -File scripts\install_scheduled_backup.ps1

    Remove:
        powershell -ExecutionPolicy Bypass -File scripts\install_scheduled_backup.ps1 -Remove

    Change the time it runs (24h clock, must be HH:mm):
        powershell -ExecutionPolicy Bypass -File scripts\install_scheduled_backup.ps1 -Time 03:30

    Two registration methods are tried, in order:
      1. S4U ("LogonType S4U") - the job runs in its own non-interactive
         session, so it also runs while nobody is logged in. This needs
         administrator rights ("Log on as a batch job").
      2. schtasks - the plain user-level job. It runs as you, and only
         while you are logged in.
#>
param(
    [string]$TaskName = "MoneyTracker Daily Backup",
    [string]$Time = "02:00",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $root "scripts\run_backup.bat"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    schtasks /Delete /TN $TaskName /F 2>$null | Out-Null
    Write-Host "Removed scheduled task '$TaskName'."
    exit 0
}

if (-not (Test-Path -LiteralPath $runner)) {
    throw "Missing $runner"
}

$parsed = [datetime]::ParseExact($Time, "H:mm", $null)
$method = "schtasks"

try {
    $action = New-ScheduledTaskAction -Execute $runner -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Daily -At $parsed
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
        -MultipleInstances IgnoreNew
    $principal = New-ScheduledTaskPrincipal `
        -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType S4U `
        -RunLevel Limited

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Force | Out-Null
    $method = "S4U (runs even when you are logged out)"
}
catch {
    Write-Host "Could not register with S4U ($($_.Exception.Message)) - falling back to schtasks."
    schtasks /Create /TN $TaskName /TR "`"$runner`"" /SC DAILY /ST $Time /F | Out-Null
}

Write-Host "Scheduled task '$TaskName' installed."
Write-Host "  runs   : every day at $Time"
Write-Host "  script : $runner"
Write-Host "  output : $root\backups\"
Write-Host "  method : $method"
Write-Host ""
Write-Host "Check it with:  Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Host "Run it now  :  Start-ScheduledTask -TaskName '$TaskName'"
