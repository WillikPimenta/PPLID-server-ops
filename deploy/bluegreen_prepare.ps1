param(
    [Parameter(Mandatory = $true)][ValidateSet('MAIN','DEV','HOM')][string]$Environment,
    [Parameter(Mandatory = $true)][string]$TargetSha,
    [Parameter(Mandatory = $true)][string]$TargetShaFull,
    [Parameter(Mandatory = $true)][string]$RunId
)
# Private preparation entrypoint. The Python orchestrator owns the shared mutex.
# No migrations, stop/start scripts or junction changes belong in this phase.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\deploy_logging.ps1')
. (Join-Path $PSScriptRoot 'lib\run_log.ps1')
. (Join-Path $PSScriptRoot 'lib\deploy_state.ps1')
Initialize-DeployRun -Environment $Environment -RunId $RunId -Trigger 'blue-green' -TargetSha $TargetSha
Initialize-DeploySteps -Environment $Environment -RunId $RunId | Out-Null
try {
    Set-DeployState -Environment $Environment -Updates @{ status='building'; runId=$RunId; targetSha=$TargetSha; startedAt=(Get-Date).ToString('o') } | Out-Null
    & (Join-Path $PSScriptRoot 'build_staging.ps1') -Environment $Environment -TargetSha $TargetSha -TargetShaFull $TargetShaFull -RunId $RunId
    Set-DeployState -Environment $Environment -Updates @{ status='validating' } | Out-Null
    & (Join-Path $PSScriptRoot 'validate_staging.ps1') -Environment $Environment -TargetSha $TargetSha -RunId $RunId
    exit 0
} catch {
    Set-DeployState -Environment $Environment -Updates @{ status='failed'; lastError=$_.Exception.Message; finishedAt=(Get-Date).ToString('o') } | Out-Null
    throw
}
