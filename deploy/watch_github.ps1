param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("MAIN", "DEV", "HOM")]
    [string]$Environment,
    [string]$RepoUrl = "https://github.com/WillikPimenta/PPLID.git"
)

$ErrorActionPreference = "Stop"
$opsRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $opsRoot "lib\paths.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_paths.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_state.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_lock.ps1")
. (Join-Path $PSScriptRoot "lib\env_spec.ps1")
. (Join-Path $PSScriptRoot "lib\run_log.ps1")
. (Join-Path $PSScriptRoot "lib\git_invoke.ps1")
. (Join-Path $opsRoot "lib\version_drift.ps1")

Initialize-PplidGitSafeDirectories
$spec = Get-PplidEnvSpec -Environment $Environment
$paths = Get-PplidDeployEnvPaths -Environment $Environment
$logFile = Join-Path (Get-PplidLogDir) "PPLID_$Environment.log"
$runId = New-DeployRunId

function Write-WatchLog {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logFile -Value "[$ts] $Message" -Encoding UTF8
}

function Ensure-Mirror {
    if (-not (Test-Path (Join-Path $paths.Mirror ".git"))) {
        Write-WatchLog "Clonando mirror $($spec.Branch)..."
        git clone -b $spec.Branch $RepoUrl $paths.Mirror 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "git clone falhou." }
    }
}

function Get-RemoteShortSha {
    Push-Location $paths.Mirror
    try {
        Invoke-PplidGit -Args @("fetch", "origin") -FailMessage "git fetch falhou." | Out-Null
        $fullLines = Invoke-PplidGit -Args @("rev-parse", "origin/$($spec.Branch)") -FailMessage "git rev-parse falhou."
        $shortLines = Invoke-PplidGit -Args @("rev-parse", "--short", "origin/$($spec.Branch)") -FailMessage "git rev-parse --short falhou."
        $full = ($fullLines -join "`n").Trim()
        $short = ($shortLines -join "`n").Trim()
        return @{ Full = $full; Short = $short }
    } finally {
        Pop-Location
    }
}

function Get-LastFailedDeployStep {
    param(
        [Parameter(Mandatory = $true)][ValidateSet("MAIN", "DEV", "HOM")][string]$Environment,
        [string]$RunId
    )
    if (-not $RunId) { return $null }
    $stepsFile = Join-Path (Get-PplidDeployEnvPaths -Environment $Environment).Logs "runs\$RunId\steps.json"
    if (-not (Test-Path $stepsFile)) { return $null }
    try {
        $steps = Get-Content $stepsFile -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($step in @($steps)) {
            if ($step.status -eq "error" -and $step.id) {
                return [string]$step.id
            }
        }
    } catch { }
    return $null
}

Write-WatchLog "=================================================="
Write-WatchLog "Watcher $Environment (run $runId)"

$legacyStatus = Get-PplidDeployStatusEnvironment -Environment $Environment
$state = Get-DeployState -Environment $Environment
$stale = Test-DeployStateStale -State $state -MaxDeployMinutes 45

if ($legacyStatus -and $legacyStatus.phase -eq "deploying") {
    $started = $legacyStatus.lastDeployStartedAt
    $legacyStale = $false
    if ($started) {
        try {
            $age = ((Get-Date) - [datetime]::Parse($started)).TotalMinutes
            $legacyStale = $age -ge 45
            if (-not $legacyStale) {
                Write-WatchLog "Legacy phase=deploying (${age}m), skip."
                exit 0
            }
        } catch { }
    } else {
        $legacyStale = $true
    }

    if ($legacyStale -or $stale) {
        Write-WatchLog "Legacy/pipeline stale, recuperando antes de continuar."
        & (Join-Path $opsRoot "recover_stuck_deploy.ps1") -Environment $Environment -Force
    }
} elseif ($stale) {
    Write-WatchLog "Pipeline stale ($($state.status)), recuperando antes de continuar."
    & (Join-Path $opsRoot "recover_stuck_deploy.ps1") -Environment $Environment -Force
}

Ensure-Mirror
$remote = Get-RemoteShortSha
$state = Sync-DeployStateFromLegacy -Environment $Environment

if (Test-PplidShaMatch -Left $remote.Short -Right ([string]$state.activeSha)) {
    Write-WatchLog "noop: origin=$($remote.Short) == active=$($state.activeSha)"
    exit 0
}

if ($state.blockedSha -and -not (Test-PplidShaMatch -Left $remote.Short -Right ([string]$state.blockedSha))) {
    Write-WatchLog "Novo commit em origin ($($remote.Short)), limpando bloqueio de $($state.blockedSha)."
    $state = Clear-DeployBlockedSha -Environment $Environment
}

$failedSameTarget = (
    $state.status -eq "failed" -and
    $state.targetSha -and
    (Test-PplidShaMatch -Left $remote.Short -Right ([string]$state.targetSha))
)
if ($failedSameTarget -and -not $state.blockedSha) {
    $failedStep = Get-LastFailedDeployStep -Environment $Environment -RunId ([string]$state.runId)
    if (-not $failedStep) { $failedStep = "deploy_failed" }
    Write-WatchLog "Backfill bloqueio para $($remote.Short) (deploy anterior falhou)."
    $state = Set-DeployState -Environment $Environment -Updates @{
        blockedSha    = $state.targetSha
        blockedAt     = if ($state.finishedAt) { $state.finishedAt } else { (Get-Date).ToString("o") }
        blockedReason = $failedStep
        blockedRunId  = $state.runId
    }
}

if (($state.blockedSha -and (Test-PplidShaMatch -Left $remote.Short -Right ([string]$state.blockedSha))) -or $failedSameTarget) {
    Write-WatchLog "Skip: origin=$($remote.Short) bloqueado apos falha em $($state.blockedReason) (run $($state.blockedRunId)); aguardando novo commit."
    exit 0
}

if (-not (Enter-DeployLock -Environment $Environment)) {
    Write-WatchLog "Lock busy, skip."
    exit 0
}

try {
    Initialize-DeployRun -Environment $Environment -RunId $runId -Trigger "watcher" -TargetSha $remote.Short
    Write-RunLog -Environment $Environment -RunId $runId -Message "Deploy necessario: $($state.activeSha) -> $($remote.Short)"
    & (Join-Path $PSScriptRoot "deploy_pipeline.ps1") -Environment $Environment -TargetSha $remote.Short -TargetShaFull $remote.Full -RunId $runId -Trigger watcher
    exit $LASTEXITCODE
} finally {
    Exit-DeployLock -Environment $Environment
}
