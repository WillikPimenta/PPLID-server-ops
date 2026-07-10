param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("MAIN", "DEV", "HOM")]
    [string]$Environment,
    [Parameter(Mandatory = $true)]
    [string]$RunId,
    [string]$Reason = "manual",
    [string]$TargetSha = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\deploy_paths.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_state.ps1")
. (Join-Path $PSScriptRoot "lib\junction.ps1")
. (Join-Path $PSScriptRoot "lib\env_spec.ps1")
. (Join-Path $PSScriptRoot "lib\run_log.ps1")
. (Join-Path $PSScriptRoot "lib\legacy_deploy_status.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_status_bridge.ps1")

$spec = Get-PplidEnvSpec -Environment $Environment
$paths = Get-PplidDeployEnvPaths -Environment $Environment
$deployScript = Join-Path $spec.RepoDir "scripts\deploy"
$state = Get-DeployState -Environment $Environment

function Log([string]$msg) {
    Write-RunLog -Environment $Environment -RunId $RunId -Message $msg -LogName "rollback.log"
}

$deployLib = Join-Path $deployScript "lib.ps1"
if (Test-Path $deployLib) {
    . $deployLib
    Update-DeployStatus -Environment $Environment -Updates @{
        phase = "deploying"
    } -EventType "rollback_started" -EventMessage "Rollback iniciado ($Reason)"
}

$previousRelease = $null
if ($TargetSha) {
    $previousRelease = Get-PplidReleaseDir -Environment $Environment -Sha $TargetSha.Trim()
}
if (-not $previousRelease -or -not (Test-Path $previousRelease)) {
    if (Test-Path $paths.Previous) {
        $target = (Get-Item $paths.Previous -Force).Target
        if ($target -is [Array]) { $target = $target[0] }
        $candidate = [string]$target
        if ($candidate -and (Test-Path $candidate)) {
            $previousRelease = $candidate
        }
    }
}
if (-not $previousRelease -or -not (Test-Path $previousRelease)) {
    if ($state.previousSha) {
        $previousRelease = Get-PplidReleaseDir -Environment $Environment -Sha ([string]$state.previousSha).Trim()
    }
}
if (-not $previousRelease -or -not (Test-Path $previousRelease)) {
    $goodSha = Get-PplidPreservedActiveSha -Environment $Environment -State $state
    if ($goodSha) {
        $previousRelease = Get-PplidReleaseDir -Environment $Environment -Sha $goodSha.Trim()
    }
}

if (-not $previousRelease -or -not (Test-Path $previousRelease)) {
    if (Test-Path $deployLib) {
        Update-DeployStatus -Environment $Environment -Updates @{
            phase             = "failed"
            lastDeployResult  = "failed"
            lastDeployMessage = "Release invalida para rollback."
            lastDeploy        = @{
                result     = "failed"
                message    = "Release invalida para rollback."
                finishedAt = (Get-Date).ToString("o")
                checks     = @()
            }
        } -EventType "rollback_failed" -EventMessage "Release invalida para rollback."
    }
    throw "Release previous invalida para rollback."
}

$rolledBackSha = Split-Path $previousRelease -Leaf
Set-DirectoryJunction -LinkPath $paths.Current -TargetPath $previousRelease
Log "current -> $previousRelease ($Reason)"

$env:PPLID_APP_ROOT = $paths.Current
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
try {
    & (Join-Path $deployScript "stop_env.ps1") -Environment $Environment 2>&1 | Out-Null
    Start-Sleep -Seconds 3
    & (Join-Path $deployScript "start_env.ps1") -Environment $Environment 2>&1 | Out-Null
    $startCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevEap
}

if (Test-Path $deployLib) {
    Set-DeployedShaFile -Environment $Environment -ShaShort $rolledBackSha
}

$healthOk = ($startCode -eq 0)
if ($healthOk) {
    try {
        & (Join-Path $deployScript "health_check.ps1") -Environment $Environment 2>&1 | Out-Null
        $healthOk = ($LASTEXITCODE -eq 0)
    } catch {
        $healthOk = $false
    }
}

$finishedAt = (Get-Date).ToString("o")
Set-DeployState -Environment $Environment -Updates @{
    status          = "rolled_back"
    activeSha       = $rolledBackSha
    lastGoodSha     = $rolledBackSha
    lastError       = $Reason
    finishedAt      = $finishedAt
    targetSha       = $null
    runId           = $null
    lockHolder      = $null
    pipelineStatus  = "rolled_back"
}

if (Test-Path $deployLib) {
    . $deployLib
    $result = if ($healthOk) { "success" } else { "failed" }
    $msg = if ($healthOk) { "Rollback para $rolledBackSha ($Reason)" } else { "Rollback falhou no health check ($Reason)" }
    $commitUpdates = @{}
    try {
        $commitUpdates = Get-CommitStatusUpdates -RepoDir $spec.RepoDir -Revision $rolledBackSha
    } catch { }
    $rollbackUpdates = @{
        phase                = if ($healthOk) { "healthy" } else { "failed" }
        lastDeployResult     = $result
        lastDeployMessage    = $msg
        lastDeployFinishedAt = $finishedAt
        deployedSha          = $rolledBackSha
        updatePending        = $false
        lastDeploy           = @{
            result     = $result
            message    = $msg
            finishedAt = $finishedAt
            checks     = @()
        }
    }
    foreach ($key in $commitUpdates.Keys) {
        $rollbackUpdates[$key] = $commitUpdates[$key]
    }
    Invoke-PipelineDeployStatusUpdate -Environment $Environment -Updates $rollbackUpdates `
        -EventType $(if ($healthOk) { "rollback_success" } else { "rollback_failed" }) -EventMessage $msg `
        -EventSha $rolledBackSha -EventSubject $commitUpdates.gitCommitSubject -EventAuthor $commitUpdates.gitCommitAuthor `
        -EventRunId $RunId
}

Log "Rollback concluido (activeSha=$rolledBackSha, healthOk=$healthOk)."

if ($healthOk) {
    . (Join-Path $PSScriptRoot "lib\sync_workspace_repo.ps1")
    Sync-PplidWorkspaceRepo -Environment $Environment -TargetSha $rolledBackSha
    Set-DeployState -Environment $Environment -Updates @{
        status         = "idle"
        pipelineStatus = $null
    }
}

if (-not $healthOk) {
    exit 1
}
