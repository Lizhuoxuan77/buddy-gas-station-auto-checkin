#Requires -Version 5.1
<#
.SYNOPSIS
    Remove the Buddy Gas Station auto check-in scheduled task.

.PARAMETER TaskName
    Scheduled task name. Default: WorkBuddy-DailyCheckin

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\uninstall_schedule.ps1
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'WorkBuddy-DailyCheckin'
)

$ErrorActionPreference = 'Stop'

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "Task '$TaskName' is not registered; nothing to remove."
    return
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Removed scheduled task '$TaskName'."