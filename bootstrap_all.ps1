$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\paths.ps1")
. (Join-Path $PSScriptRoot "lib\sync_lock.ps1")
. (Join-Path $PSScriptRoot "deploy\lib\deploy_lock.ps1")

if (-not (Enter-PplidSyncLock)) {
    Write-Host "Sync ativo; bootstrap adiado."
    exit 0
}
Exit-PplidSyncLock

if (-not (Enter-PplidOrchestratorLock)) {
    Write-Host "Bootstrap ja em execucao (outro processo segura o lock). Encerre PowerShell preso ou reinicie a sessao."
    exit 1
}

try {
    & (Join-Path $PSScriptRoot "deploy\init_deploy_layout.ps1") -Environment ALL
    foreach ($env in @("MAIN", "DEV", "HOM")) {
        Write-Host "=== Bootstrap $env ==="
        & (Join-Path $PSScriptRoot "deploy\bootstrap_env.ps1") -Environment $env
    }
    Write-Host "Bootstrap concluido."
} finally {
    Exit-PplidOrchestratorLock
}
