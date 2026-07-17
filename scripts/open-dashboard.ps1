$ErrorActionPreference = "Stop"
$protocol = Split-Path $PSScriptRoot -Parent

Push-Location $protocol
try {
    & uv run --locked sb dashboard open | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "The local second-brain dashboard could not be opened."
    }
}
finally {
    Pop-Location
}
