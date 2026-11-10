#Requires -Version 5.1
<#
.SYNOPSIS
    Register a daily scheduled task that runs Buddy Gas Station auto check-in.

.DESCRIPTION
    Runs scripts/checkin.py with pythonw.exe (no console window) at the given
    times. The task runs in the current user's interactive session, so
    WorkBuddy must be running and logged in when it fires.

.PARAMETER TaskName
    Scheduled task name. Default: WorkBuddy-DailyCheckin

.PARAMETER Times
    One or more daily start times, 24-hour format. Default: 10:00, 20:00

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install_schedule.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install_schedule.ps1 -Times 09:30,21:00
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'WorkBuddy-DailyCheckin',
    [string[]]$Times = @('10:00', '20:00')
)

$ErrorActionPreference = 'Stop'

$script = Join-Path $PSScriptRoot 'checkin.py'
if (-not (Test-Path -LiteralPath $script)) {
    throw "checkin.py not found next to this installer: $script"
}

function Resolve-Pythonw {
    $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\pythonw.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\pythonw.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\pythonw.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs/WorkBuddy/resources/vendor/python/pythonw.exe'),
        'C:\Python313\pythonw.exe',
        'C:\Python312\pythonw.exe'
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    throw 'pythonw.exe not found. Install Python 3 or add it to PATH.'
}

$pythonw = Resolve-Pythonw
$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument ('-I "{0}" --quiet --json' -f $script)
$triggers = @($Times | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ })
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Settings $settings -Principal $principal -Force | Out-Null

Write-Host "Registered task '$TaskName'"
Write-Host "  pythonw : $pythonw"
Write-Host "  script  : $script"
Write-Host "  times   : $($Times -join ', ')"
Get-ScheduledTaskInfo -TaskName $TaskName |
    Select-Object TaskName, NextRunTime |
    Format-List