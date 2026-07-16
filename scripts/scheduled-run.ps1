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
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
