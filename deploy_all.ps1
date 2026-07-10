$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\paths.ps1")

$repoDir = Get-PplidRepoDir -Name "PPLID_DEV"
$deployScript = Join-Path $repoDir "scripts\deploy\deploy_env.ps1"

if (-not (Test-Path $deployScript)) {
    throw "Script de deploy nao encontrado: $deployScript"
}

foreach ($env in @("MAIN", "DEV", "HOM")) {
    Write-Host "=== Deploy $env ==="
    & $deployScript -Environment $env
    if ($LASTEXITCODE -ne 0) {
        throw "Deploy falhou para $env (exit $LASTEXITCODE)"
    }
}

Write-Host "Deploy concluido para MAIN, DEV e HOM."
