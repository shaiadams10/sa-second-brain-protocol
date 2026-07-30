[CmdletBinding()]
param(
    [double]$DailyLimitUsd = 2.0,
    [double]$MonthlyKeyLimitUsd = 10.0
)

$ErrorActionPreference = "Stop"
$apiBase = "https://openrouter.ai/api/v1"
$secretDir = Join-Path $env:LOCALAPPDATA "PersonalSecondBrain\secrets"
$managementPath = Join-Path $secretDir "openrouter-management.dpapi"
$runtimePath = Join-Path $secretDir "openrouter-runtime.dpapi"
$guardrailName = "Personal Second Brain Automation"
$keyName = "Personal Second Brain Runtime"
$createdGuardrail = $false
$createdKeyHash = $null
$guardrailData = $null

if (-not (Test-Path -LiteralPath $managementPath)) {
    throw "The encrypted OpenRouter management key was not found."
}
if (Test-Path -LiteralPath $runtimePath) {
    throw "A runtime key is already stored locally; refusing to overwrite it."
}

$encryptedManagement = (Get-Content -Raw -LiteralPath $managementPath).Trim()
$secureManagement = ConvertTo-SecureString -String $encryptedManagement
$keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureManagement)
try {
    $managementKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
}

$headers = @{
    Authorization = "Bearer $managementKey"
    "Content-Type" = "application/json"
}

try {
    $guardrailBody = @{
        name = $guardrailName
        description = "Restricted unattended inference for the local second-brain scheduler."
        allowed_models = @(
            "deepseek/deepseek-v4-flash"
            "openai/gpt-5.6-sol"
        )
        allowed_providers = $null
        enforce_zdr_openai = $true
        enforce_zdr_other = $true
        enforce_zdr_anthropic = $true
        enforce_zdr_google = $true
        ignored_models = $null
        ignored_providers = $null
        limit_usd = $DailyLimitUsd
        reset_interval = "daily"
    } | ConvertTo-Json -Depth 6

    $guardrails = Invoke-RestMethod `
        -Method Get `
        -Uri "$apiBase/guardrails?limit=100" `
        -Headers $headers
    $matchingGuardrails = @(
        $guardrails.data | Where-Object { $_.name -eq $guardrailName }
    )
    if ($matchingGuardrails.Count -gt 1) {
        throw "More than one matching guardrail exists; refusing an ambiguous update."
    }

    if ($matchingGuardrails.Count -eq 1) {
        $guardrail = Invoke-RestMethod `
            -Method Patch `
            -Uri "$apiBase/guardrails/$($matchingGuardrails[0].id)" `
            -Headers $headers `
            -Body $guardrailBody
    }
    else {
        $guardrail = Invoke-RestMethod `
            -Method Post `
            -Uri "$apiBase/guardrails" `
            -Headers $headers `
            -Body $guardrailBody
        $createdGuardrail = $true
    }
    $guardrailData = if ($guardrail.data) { $guardrail.data } else { $guardrail }

    $keys = Invoke-RestMethod `
        -Method Get `
        -Uri "$apiBase/keys?limit=100" `
        -Headers $headers
    $existingNames = @($keys.data | ForEach-Object { $_.name })
    if ($existingNames -contains $keyName) {
        $keyName = "$keyName $(Get-Date -Format 'yyyyMMdd-HHmmss')"
    }

    $keyBody = @{
        name = $keyName
        limit = $MonthlyKeyLimitUsd
        limit_reset = "monthly"
        include_byok_in_limit = $true
    } | ConvertTo-Json
    $keyResponse = Invoke-RestMethod `
        -Method Post `
        -Uri "$apiBase/keys" `
        -Headers $headers `
        -Body $keyBody

    $createdKeyHash = [string]$keyResponse.data.hash
    $runtimeKey = [string]$keyResponse.key
    if (-not $createdKeyHash -or -not $runtimeKey) {
        throw "OpenRouter did not return the new runtime key and hash."
    }

    $secureRuntime = ConvertTo-SecureString -String $runtimeKey -AsPlainText -Force
    $encryptedRuntime = ConvertFrom-SecureString -SecureString $secureRuntime
    Set-Content `
        -LiteralPath $runtimePath `
        -Value $encryptedRuntime `
        -Encoding ASCII
    Remove-Variable runtimeKey, encryptedRuntime -ErrorAction SilentlyContinue

    $assignmentBody = @{
        key_hashes = @($createdKeyHash)
    } | ConvertTo-Json
    Invoke-RestMethod `
        -Method Post `
        -Uri "$apiBase/guardrails/$($guardrailData.id)/assignments/keys" `
        -Headers $headers `
        -Body $assignmentBody |
        Out-Null

    $assignments = Invoke-RestMethod `
        -Method Get `
        -Uri "$apiBase/guardrails/$($guardrailData.id)/assignments/keys?limit=100" `
        -Headers $headers
    $assigned = @(
        $assignments.data |
            Where-Object { $_.key_hash -eq $createdKeyHash }
    ).Count -eq 1
    if (-not $assigned) {
        throw "The runtime-key guardrail assignment could not be verified."
    }

    [pscustomobject]@{
        configured = $true
        runtime_key_name = $keyName
        key_monthly_limit_usd = $MonthlyKeyLimitUsd
        guardrail = $guardrailData.name
        guardrail_daily_limit_usd = $guardrailData.limit_usd
        allowed_models = @($guardrailData.allowed_models)
        zdr_openai = [bool]$guardrailData.enforce_zdr_openai
        zdr_other = [bool]$guardrailData.enforce_zdr_other
        assignment_verified = $assigned
        runtime_secret_stored = (Test-Path -LiteralPath $runtimePath)
    } | ConvertTo-Json -Depth 5
}
catch {
    if ($createdKeyHash) {
        try {
            Invoke-RestMethod `
                -Method Delete `
                -Uri "$apiBase/keys/$createdKeyHash" `
                -Headers $headers |
                Out-Null
        }
        catch {
            # The created key still has the monthly limit if cleanup is unavailable.
        }
    }
    if (Test-Path -LiteralPath $runtimePath) {
        Remove-Item -LiteralPath $runtimePath -Force
    }
    if ($createdGuardrail -and $guardrailData.id) {
        try {
            Invoke-RestMethod `
                -Method Delete `
                -Uri "$apiBase/guardrails/$($guardrailData.id)" `
                -Headers $headers |
                Out-Null
        }
        catch {
            # An unassigned guardrail has no effect on other API keys.
        }
    }
    $status = if ($_.Exception.Response) {
        [int]$_.Exception.Response.StatusCode
    }
    else {
        0
    }
    throw "OpenRouter provisioning failed (HTTP $status): $($_.Exception.Message)"
}
finally {
    $headers.Clear()
    Remove-Variable managementKey, encryptedManagement -ErrorAction SilentlyContinue
}
