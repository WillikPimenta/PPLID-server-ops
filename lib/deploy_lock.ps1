# Compat: reexporta lock por ambiente do pipeline Railway-like.
. (Join-Path (Split-Path $PSScriptRoot -Parent) "deploy\lib\deploy_lock.ps1")

function Enter-PplidDeployLock {
    param(
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment = "DEV"
    )
    return (Enter-DeployLock -Environment $Environment)
}

function Exit-PplidDeployLock {
    param(
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment = "DEV"
    )
    Exit-DeployLock -Environment $Environment
}
