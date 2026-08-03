param([switch]$Canary)

$ErrorActionPreference = "Stop"
$protocol = Split-Path $PSScriptRoot -Parent
$vault = Split-Path $protocol -Parent
$runtime = Join-Path $env:LOCALAPPDATA "PersonalSecondBrain"
$logDir = Join-Path $runtime "runs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$log = Join-Path $logDir "scheduled-$stamp.log"
New-Item -ItemType File -Force -Path $log | Out-Null
$finalExit = 1
$startedAt = Get-Date

try { $Host.UI.RawUI.WindowTitle = "Personal Second Brain - Scheduled Run" } catch {}

function Write-RunLine {
    param([string]$Text = "", [ConsoleColor]$Color = [ConsoleColor]::Gray)
    Write-Host $Text -ForegroundColor $Color
    $Text | Out-File -LiteralPath $log -Append -Encoding utf8
}

function Write-RunSection {
    param([string]$Title, [string]$Detail = "")
    $width = 76
    $border = "=" * $width
    Write-RunLine ""
    Write-RunLine $border DarkCyan
    Write-RunLine ("  {0}  |  {1}" -f $Title.ToUpperInvariant(), (Get-Date -Format "h:mm:ss tt")) Cyan
    if ($Detail) { Write-RunLine ("  {0}" -f $Detail) DarkGray }
    Write-RunLine $border DarkCyan
}

function Invoke-LoggedSbCommand {
    param([string]$Label, [string[]]$Arguments)
    Write-RunLine ("-> {0}" -f $Label) Yellow
    $ErrorActionPreference = "Continue"
    & uv run --locked sb @Arguments *>&1 | Tee-Object -FilePath $log -Append | ForEach-Object { Write-Host $_ }
    return $LASTEXITCODE
}

function Add-DailySummary {
    $dailyPath = Join-Path $vault ("Journal/Daily/{0}.md" -f (Get-Date -Format "yyyy-MM-dd"))
    Write-RunSection "What the Brain learned today" "A concise extract from today's generated Daily note."
    if (-not (Test-Path -LiteralPath $dailyPath)) {
        Write-RunLine "No Daily note was produced for today." DarkYellow
        return
    }
    $capturing = $false
    $shown = 0
    foreach ($line in Get-Content -LiteralPath $dailyPath -Encoding utf8) {
        if ($line -match '^### (Quick activity recap|What the brain learned)$') {
            $capturing = $true
            Write-RunLine $line White
            continue
        }
        if ($capturing -and $line -match '^### ') { $capturing = $false }
        if ($capturing -and $line -notmatch '^<!-- sb:generated') {
            Write-RunLine $line Gray
            $shown++
            if ($shown -ge 60) {
                Write-RunLine "...additional detail is available in today's Daily note and dashboard." DarkGray
                break
            }
        }
    }
    if ($shown -eq 0) { Write-RunLine "Today's note contains no new generated learning summary." DarkYellow }
}

Clear-Host
Write-RunLine ("  SHAI SECOND BRAIN  /  {0}" -f $(if ($Canary) { "HEALTH CANARY" } else { "SCHEDULED DAILY" })) Cyan
Write-RunLine ("  Started: {0}" -f $startedAt.ToString("dddd, MMMM d, yyyy 'at' h:mm:ss tt")) White
Write-RunLine ("  Live log: {0}" -f $log) DarkGray

Push-Location $protocol
try {
    Write-RunSection "1 / 3 - Brain pipeline" $(if ($Canary) { "Checking runtime health only." } else { "Collecting evidence, synthesizing knowledge, validating, and publishing safely." })
    $command = if ($Canary) { "health" } else { "scheduled" }
    $pipelineExit = Invoke-LoggedSbCommand "Running Brain $command workflow" @($command)
    $finalExit = $pipelineExit
    Write-RunLine ("Pipeline exit code: {0}" -f $pipelineExit) $(if ($pipelineExit -eq 0) { "Green" } else { "Red" })

    if (-not $Canary) {
        Write-RunSection "2 / 3 - Dashboard" "Rebuilding the private local overview with the latest validated state."
        $dashboardExit = Invoke-LoggedSbCommand "Refreshing dashboard" @("dashboard", "build")
        if ($dashboardExit -ne 0) {
            Write-RunLine "Dashboard refresh failed; the pipeline result is preserved." Red
            if ($finalExit -eq 0) { $finalExit = 1 }
        }

        Write-RunSection "3 / 3 - Public protocol" "Syncing sanitized generic protocol changes only when needed."
        $publishExit = Invoke-LoggedSbCommand "Checking public protocol draft" @("protocol", "publish", "--if-changed")
        if ($publishExit -ne 0) {
            Write-RunLine "Public protocol draft sync failed; it will retry on the next scheduled run." Red
            if ($finalExit -eq 0) { $finalExit = 1 }
        }

        Add-DailySummary
    }
}
catch {
    $finalExit = 1
    Write-RunSection "Unexpected failure"
    Write-RunLine ("{0}: {1}" -f $_.Exception.GetType().Name, $_.Exception.Message) Red
    Write-RunLine "The error is recorded in the log. Evidence checkpoints remain protected." DarkYellow
}
finally {
    Pop-Location
    $elapsed = (Get-Date) - $startedAt
    $outcome = if ($finalExit -eq 0) { "SUCCESS" } else { "FAILED" }
    Write-RunSection "Run complete"
    Write-RunLine ("Outcome:  {0}" -f $outcome) $(if ($finalExit -eq 0) { "Green" } else { "Red" })
    Write-RunLine ("Duration: {0:hh\:mm\:ss}" -f $elapsed) White
    Write-RunLine ("Log:      {0}" -f $log) DarkGray
    if (-not $Canary) {
        $viewer = Join-Path $PSScriptRoot "scheduled-run-viewer.ps1"
        $arguments = @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"{0}"' -f $viewer),
            "-LogPath", ('"{0}"' -f $log), "-ExitCode", [string]$finalExit
        )
        Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -WindowStyle Normal | Out-Null
    }
}

exit $finalExit
