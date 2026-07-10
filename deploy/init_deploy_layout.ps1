param(
    [ValidateSet("MAIN", "DEV", "HOM", "ALL")]
    [string]$Environment = "ALL"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\deploy_paths.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_state.ps1")
. (Join-Path $PSScriptRoot "lib\env_spec.ps1")
. (Join-Path (Split-Path $PSScriptRoot -Parent) "lib\version_drift.ps1")

$envList = if ($Environment -eq "ALL") { @("MAIN", "DEV", "HOM") } else { @($Environment) }

foreach ($env in $envList) {
    Initialize-PplidDeployLayout -Environment $env
    Sync-DeployStateFromLegacy -Environment $env
    Write-Host "Layout OK: $env"
}
