[CmdletBinding()]
param(
    [string]$TaskName = "Personal Second Brain Daily"
)

$ErrorActionPreference = "Stop"
$secretRoot = [IO.Path]::GetFullPath(
    (Join-Path $env:LOCALAPPDATA "PersonalSecondBrain\secrets")
)
$managementPath = [IO.Path]::GetFullPath(
    (Join-Path $secretRoot "openrouter-management.dpapi")
)
$runtimePath = [IO.Path]::GetFullPath(
    (Join-Path $secretRoot "openrouter-runtime.dpapi")
)

if ([IO.Path]::GetDirectoryName($managementPath) -ne $secretRoot) {
    throw "Management-key cleanup target escaped the secrets directory."
}
if (-not (Test-Path -LiteralPath $runtimePath)) {
    throw "The encrypted runtime key is missing; refusing to finalize."
}
if (Test-Path -LiteralPath $managementPath) {
    Remove-Item -LiteralPath $managementPath -Force
}
if (Test-Path -LiteralPath $managementPath) {
    throw "Management-key cleanup failed."
}

Enable-ScheduledTask -TaskName $TaskName | Out-Null
$task = Get-ScheduledTask -TaskName $TaskName
$taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName

[pscustomobject]@{
    management_key_removed = -not (Test-Path -LiteralPath $managementPath)
    runtime_key_stored = Test-Path -LiteralPath $runtimePath
    task_state = [string]$task.State
    next_run = $taskInfo.NextRunTime.ToString("o")
} | ConvertTo-Json
