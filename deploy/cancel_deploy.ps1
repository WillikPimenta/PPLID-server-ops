param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("MAIN", "DEV", "HOM")]
    [string]$Environment,
    [string]$RequestedBy = "console"
)

$ErrorActionPreference = "Stop"
$opsRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $opsRoot "lib\paths.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_state.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_paths.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_lock.ps1")
. (Join-Path $PSScriptRoot "lib\run_log.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_logging.ps1")
. (Join-Path $PSScriptRoot "lib\legacy_deploy_status.ps1")

$busyStatuses = @("building", "validating", "promoting", "watching")
$state = Get-DeployState -Environment $Environment
$runId = [string]$state.runId
$wasBusy = $state.status -in $busyStatuses

if (-not $wasBusy) {
    Clear-DeployCancelRequested -Environment $Environment
    Write-Output (@{
        ok            = $true
        environment   = $Environment
        previousRunId = $runId
        message       = "Nenhum deploy em andamento."
        alreadyIdle   = $true
    } | ConvertTo-Json -Compress)
    exit 0
}

Write-DeployCancelRequested -Environment $Environment -RunId $runId -RequestedBy $RequestedBy | Out-Null

$pidToKill = $null
if ($runId) {
    $manifestPath = Join-Path (Get-PplidDeployRunDir -Environment $Environment -RunId $runId) "manifest.json"
    if (Test-Path $manifestPath) {
        try {
            $manifest = Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($manifest.pid) { $pidToKill = [int]$manifest.pid }
        } catch { }
    }
}

$killed = $false
if ($pidToKill -and $pidToKill -gt 0) {
    try {
        $proc = Get-Process -Id $pidToKill -ErrorAction SilentlyContinue
        if ($proc) {
            # Mata a arvore do pipeline (pip/npm/git filhos).
            & taskkill.exe /F /T /PID $pidToKill 2>$null | Out-Null
            Start-Sleep -Milliseconds 400
            if (Get-Process -Id $pidToKill -ErrorAction SilentlyContinue) {
                Stop-Process -Id $pidToKill -Force -ErrorAction SilentlyContinue
            }
            $killed = $true
        }
    } catch {
        $killed = $false
    }
}

$finishedAt = (Get-Date).ToString("o")
$preservedSha = Get-PplidPreservedActiveSha -Environment $Environment -State $state
$targetSha = [string]$state.targetSha
$startedAt = [string]$state.startedAt
$durationSec = 0
if ($startedAt) {
    try {
        $durationSec = [int]([datetime]::Parse($finishedAt) - [datetime]::Parse($startedAt)).TotalSeconds
    } catch { }
}

if ($runId) {
    try {
        Skip-RemainingDeploySteps -Environment $Environment -RunId $runId -AfterStepId ""
    } catch { }

    try {
        Update-DeployManifest -Environment $Environment -RunId $runId -Updates @{
            finishedAt = $finishedAt
            result     = "cancelled"
            cancelledBy = $RequestedBy
        }
    } catch { }

    try {
        Write-DeployRunSummary -Environment $Environment -RunId $runId -Summary @{
            environment = $Environment
            runId       = $runId
            fromSha     = $preservedSha
            toSha       = $targetSha
            startedAt   = $startedAt
            finishedAt  = $finishedAt
            durationSec = $durationSec
            result      = "cancelled"
            failedStep  = $null
            lastError   = "cancelled_by_user"
            trigger     = "cancel"
        }
    } catch { }

    try {
        Write-DeployLogInfo -Environment $Environment -RunId $runId -Message "Deploy cancelado por $RequestedBy" -LogName "pipeline.log"
    } catch { }
}

$updates = @{
    status     = "idle"
    lastError  = "cancelled_by_user"
    finishedAt = $finishedAt
    lockHolder = $null
    targetSha  = $null
    runId      = $null
    startedAt  = $null
}
if ($preservedSha) {
    $updates.activeSha = $preservedSha
    $updates.lastGoodSha = $preservedSha
}
Set-DeployState -Environment $Environment -Updates $updates
Clear-DeployBlockedSha -Environment $Environment | Out-Null

try {
    $statusPath = Get-PplidStatusFile
    if (Test-Path $statusPath) {
        $status = Get-Content $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $envStatus = $status.environments.$Environment
        if ($envStatus -and $envStatus.phase -eq "deploying") {
            $envStatus.phase = if ($preservedSha) { "healthy" } else { "failed" }
            $envStatus.lastDeployResult = "cancelled"
            $envStatus.lastDeployMessage = "cancelled_by_user"
            $envStatus.lastDeployFinishedAt = $finishedAt
            if ($preservedSha) {
                $envStatus.deployedSha = $preservedSha
                $envStatus.updatePending = $false
            }
            $status.updatedAt = $finishedAt
            $utf8 = New-Object System.Text.UTF8Encoding $false
            [System.IO.File]::WriteAllText($statusPath, ($status | ConvertTo-Json -Depth 20), $utf8)
        }
    }
} catch { }

Clear-DeployCancelRequested -Environment $Environment

Write-Output (@{
    ok            = $true
    environment   = $Environment
    previousRunId = $runId
    killedPid     = $pidToKill
    processKilled = $killed
    message       = "Deploy cancelado. Ambiente liberado para novo redeploy."
    alreadyIdle   = $false
} | ConvertTo-Json -Compress)
exit 0
