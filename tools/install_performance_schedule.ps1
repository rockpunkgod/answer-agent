param(
    [Parameter(Mandatory=$true)][string]$Config,
    [string]$TaskName = 'EnglishHelpdesk-DailyPerformanceDraft',
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    return
}
$projectRoot = Split-Path -Parent $PSScriptRoot
$configPath = (Resolve-Path -LiteralPath $Config).Path
$windowlessPython = Get-Command python -CommandType Application | ForEach-Object {
    Join-Path (Split-Path -Parent $_.Source) 'pythonw.exe'
} | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $windowlessPython) {
    throw 'pythonw.exe is required to run the local schedule without a console window.'
}
$existingTask = Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction SilentlyContinue
# A short periodic trigger delegates time zone and catch-up decisions to Python.
# It never accesses WeCom, submits institution forms or sends reports.
$action = New-ScheduledTaskAction -Execute $windowlessPython `
    -Argument ('-m tools.performance_report_schedule --config "' + $configPath + '"') `
    -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 15)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited
if ($existingTask) {
    # Never take over a same-named task belonging to another command or project.
    if ($existingTask.Actions.Count -ne 1 -or
        $existingTask.Actions[0].Arguments -notmatch '^-m tools\.performance_report_schedule --config ' -or
        $existingTask.Actions[0].WorkingDirectory -ne $projectRoot) {
        throw 'Same-named task belongs to another command or project; refusing replacement.'
    }
    Set-ScheduledTask -TaskName $TaskName -TaskPath '\' -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal | Out-Null
    Enable-ScheduledTask -TaskName $TaskName -TaskPath '\' | Out-Null
    $operation = 'UPDATED_EXISTING'
} else {
    Register-ScheduledTask -TaskName $TaskName -TaskPath '\' -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal `
        -Description 'Local-only daily performance draft; no message sending or external submission.' | Out-Null
    $operation = 'REGISTERED'
}
[pscustomobject]@{TaskName=$TaskName;Operation=$operation;Pythonw=$windowlessPython;Config=$configPath;Enabled=$true} | ConvertTo-Json
