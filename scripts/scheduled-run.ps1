param([switch]$Canary)

$ErrorActionPreference = "Stop"
$protocol = Split-Path $PSScriptRoot -Parent
$runtime = Join-Path $env:LOCALAPPDATA "PersonalSecondBrain"
$logDir = Join-Path $runtime "runs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$log = Join-Path $logDir "scheduled-$stamp.log"
Push-Location $protocol
try {
    $command = if ($Canary) { "health" } else { "scheduled" }
    & uv run --locked sb $command *>&1 | Tee-Object -FilePath $log
    $pipelineExit = $LASTEXITCODE
    $finalExit = $pipelineExit
    if (-not $Canary) {
        & uv run --locked sb dashboard build *>&1 | Tee-Object -FilePath $log -Append
        if ($LASTEXITCODE -ne 0) {
            "Dashboard refresh failed; the pipeline result is preserved." | Tee-Object -FilePath $log -Append
        }
        & uv run --locked sb protocol publish --if-changed *>&1 | Tee-Object -FilePath $log -Append
        if ($LASTEXITCODE -ne 0) {
            "Public protocol draft sync failed; it will retry on the next scheduled run." | Tee-Object -FilePath $log -Append
            if ($finalExit -eq 0) { $finalExit = 1 }
        }
    }
    exit $finalExit
}
finally {
    Pop-Location
}
