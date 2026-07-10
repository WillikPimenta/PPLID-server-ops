$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\paths.ps1")

foreach ($env in @("MAIN", "DEV", "HOM")) {
    Write-Host "=== Sync $env ==="
    & (Join-Path $PSScriptRoot "update_repo.ps1") -Environment $env
    if ($LASTEXITCODE -ne 0) {
        throw "Sync falhou para $env (exit $LASTEXITCODE)"
    }
}

Write-Host "Sync concluido para MAIN, DEV e HOM."
