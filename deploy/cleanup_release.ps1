param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("MAIN", "DEV", "HOM")]
    [string]$Environment,
    [string]$Sha = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\deploy_paths.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_state.ps1")
. (Join-Path $PSScriptRoot "lib\release_cleanup.ps1")
. (Join-Path $PSScriptRoot "lib\legacy_deploy_status.ps1")

$paths = Get-PplidDeployEnvPaths -Environment $Environment

if ($Sha) {
    $dirs = @(Get-PplidReleaseDir -Environment $Environment -Sha $Sha)
} else {
    Write-Warning "Sem -Sha: use prune_releases.ps1 para retencao segura. Abortando."
    exit 1
}

foreach ($dir in $dirs) {
    Write-Host "Removendo release: $dir"
    Remove-PplidReleaseDir -Environment $Environment -ReleaseDir $dir
}

Sync-DeployStateFromLegacy -Environment $Environment | Out-Null
Write-Host "Cleanup concluido para $Environment."
