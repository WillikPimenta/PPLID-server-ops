param(
    [ValidateSet("MAIN", "DEV", "HOM", "ALL")]
    [string]$Environment = "ALL",
    [switch]$RestartServices,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$opsRoot = $PSScriptRoot
. (Join-Path $opsRoot "lib\paths.ps1")
. (Join-Path $opsRoot "lib\version_drift.ps1")
. (Join-Path $opsRoot "deploy\lib\deploy_state.ps1")
. (Join-Path $opsRoot "deploy\lib\deploy_lock.ps1")
. (Join-Path $opsRoot "deploy\lib\env_spec.ps1")
. (Join-Path $opsRoot "deploy\lib\legacy_deploy_status.ps1")

$envList = if ($Environment -eq "ALL") { @("MAIN", "DEV", "HOM") } else { @($Environment) }
$statusPath = Get-PplidStatusFile

function Write-RecoveryLog {
    param([string]$Message)
    $logFile = Join-Path (Get-PplidLogDir) "recover_deploy.log"
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logFile -Value "[$ts] $Message" -Encoding UTF8
    Write-Host $Message
}

foreach ($env in $envList) {
    Write-RecoveryLog "=== Recover $env ==="

    $state = Get-DeployState -Environment $env
    $stale = Test-DeployStateStale -State $state -MaxDeployMinutes 45
    if ($state.status -eq "promoting" -and $state.updatedAt) {
        try {
            if (((Get-Date) - [datetime]::Parse([string]$state.updatedAt)).TotalMinutes -ge 8) {
                $stale = $true
            }
        } catch {
            $stale = $true
        }
    }
    $busy = $state.status -in @("building", "validating", "promoting") -or $stale
    $preservedSha = Get-PplidPreservedActiveSha -Environment $env -State $state

    if ($busy -or $Force) {
        $updates = @{
            status     = if ($preservedSha) { "idle" } else { "failed" }
            lastError  = if ($stale) { "recovered_stale" } else { "recovered_manual" }
            finishedAt = (Get-Date).ToString("o")
            lockHolder = $null
            targetSha  = $null
            runId      = $null
            startedAt  = $null
        }
        if ($preservedSha) {
            $updates.activeSha = $preservedSha
            $updates.lastGoodSha = $preservedSha
        }
        Set-DeployState -Environment $env -Updates $updates
        Clear-DeployBlockedSha -Environment $env | Out-Null
        Write-RecoveryLog "deploy-state.json reset for $env (activeSha=$preservedSha, blockedSha cleared)"
    } elseif ($state.blockedSha) {
        Clear-DeployBlockedSha -Environment $env | Out-Null
        Write-RecoveryLog "blockedSha cleared for $env (was $($state.blockedSha))"
    }

    if (Test-Path $statusPath) {
        $status = Get-Content $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $envStatus = $status.environments.$env
        if ($envStatus -and ($envStatus.phase -eq "deploying" -or $Force)) {
            $envStatus.phase = if ($preservedSha) { "healthy" } else { "failed" }
            $envStatus.lastDeployResult = if ($preservedSha) { "success" } else { "failed" }
            $envStatus.lastDeployMessage = "recovered_stuck_deploy"
            $envStatus.lastDeployFinishedAt = (Get-Date).ToString("o")
            if ($preservedSha) {
                $envStatus.deployedSha = $preservedSha
                $envStatus.updatePending = $false
            }
            $status.updatedAt = (Get-Date).ToString("o")
            $utf8 = New-Object System.Text.UTF8Encoding $false
            [System.IO.File]::WriteAllText($statusPath, ($status | ConvertTo-Json -Depth 20), $utf8)
            Write-RecoveryLog "deploy-status.json phase reset for $env"
        }
    }

    Sync-DeployStateFromLegacy -Environment $env | Out-Null

    $stateAfter = Get-DeployState -Environment $env
    if (-not $stateAfter.activeSha -and $preservedSha) {
        Set-DeployState -Environment $env -Updates @{
            activeSha   = $preservedSha
            lastGoodSha = $preservedSha
            status      = "idle"
            lastError   = $null
        }
        Write-RecoveryLog "activeSha restaurado para $preservedSha"
    }

    if ($RestartServices) {
        $spec = Get-PplidEnvSpec -Environment $env
        $deployScript = Join-Path $spec.RepoDir "scripts\deploy"
        if (Test-Path (Join-Path $deployScript "start_env.ps1")) {
            & (Join-Path $deployScript "stop_env.ps1") -Environment $env
            & (Join-Path $deployScript "start_env.ps1") -Environment $env
            Write-RecoveryLog "Services restarted for $env"
        }
    }
}

Write-RecoveryLog "Recovery concluido."
