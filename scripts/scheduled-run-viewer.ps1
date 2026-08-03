param(
    [Parameter(Mandatory = $true)][string]$LogPath,
    [int]$ExitCode = 1
)

$ErrorActionPreference = "Continue"
try { $Host.UI.RawUI.WindowTitle = "Personal Second Brain - Run Report" } catch {}
Clear-Host

$color = if ($ExitCode -eq 0) { "Green" } else { "Red" }
$label = if ($ExitCode -eq 0) { "RUN COMPLETED SUCCESSFULLY" } else { "RUN NEEDS ATTENTION" }
$border = "=" * 76
Write-Host $border -ForegroundColor DarkCyan
Write-Host ("  SHAI SECOND BRAIN  /  {0}" -f $label) -ForegroundColor $color
Write-Host $border -ForegroundColor DarkCyan
Write-Host ""

if (Test-Path -LiteralPath $LogPath) {
    Get-Content -LiteralPath $LogPath -Encoding utf8
} else {
    Write-Host "The run log could not be found." -ForegroundColor Red
}

Write-Host ""
Write-Host $border -ForegroundColor DarkCyan
Write-Host "  This report is separate from the scheduled task." -ForegroundColor White
Write-Host "  Leaving it open will not block tomorrow's run." -ForegroundColor Green
Write-Host "  Close this window when you are finished reviewing the run." -ForegroundColor Cyan
Write-Host $border -ForegroundColor DarkCyan
Write-Host ""
[void](Read-Host "Press Enter to close")
