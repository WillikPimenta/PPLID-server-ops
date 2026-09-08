<#
.SYNOPSIS
  Valida que VITE_DEV_SERVER_PORT sozinha NAO muda a porta do frontend deployado,
  e que frontendPort em ops/config/env.config.json e a fonte efetiva (via Get-EnvironmentConfig).
#>
param(
    [ValidateSet("MAIN", "DEV", "HOM")]
    [string]$Environment = "DEV"
)

$ErrorActionPreference = "Stop"
$opsRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not (Test-Path (Join-Path $opsRoot "lib\paths.ps1"))) {
    $opsRoot = "C:\PPLID\ops"
}
. (Join-Path $opsRoot "lib\paths.ps1")

$repoDir = Get-PplidRepoDir -Name "PPLID_$Environment"
$libPath = Join-Path $repoDir "scripts\deploy\lib.ps1"
if (-not (Test-Path $libPath)) {
    throw "lib.ps1 nao encontrado: $libPath"
}
. $libPath

$cfg = Get-EnvironmentConfig -Environment $Environment
$opsCfgPath = Get-PplidEnvConfigPath
$opsRootJson = Get-Content $opsCfgPath -Raw | ConvertFrom-Json
$opsPort = [int]$opsRootJson.$Environment.frontendPort

Write-Host "Environment: $Environment"
Write-Host "ops/config frontendPort: $opsPort"
Write-Host "Get-EnvironmentConfig FrontendPort: $($cfg.FrontendPort)"
Write-Host "config source: $opsCfgPath"

if ($cfg.FrontendPort -ne $opsPort) {
    Write-Host "FAIL: Get-EnvironmentConfig nao esta lendo ops/config/env.config.json" -ForegroundColor Red
    exit 1
}
Write-Host "OK: FrontendPort unificado com ops/config/env.config.json" -ForegroundColor Green

$sharedFe = Join-Path (Get-PplidBaseDir) "deploy\$Environment\shared\frontend.env"
$vitePort = $null
if (Test-Path $sharedFe) {
    Get-Content $sharedFe | ForEach-Object {
        if ($_ -match '^\s*VITE_DEV_SERVER_PORT\s*=\s*(.+)\s*$') {
            $vitePort = [int]$Matches[1].Trim()
        }
    }
}
Write-Host "shared VITE_DEV_SERVER_PORT: $vitePort"

$listening = [bool](Get-NetTCPConnection -LocalPort $cfg.FrontendPort -State Listen -ErrorAction SilentlyContinue)
Write-Host "Listening on FrontendPort $($cfg.FrontendPort): $listening"

if ($vitePort -and $vitePort -ne $cfg.FrontendPort) {
    Write-Host ("NOTE: VITE_DEV_SERVER_PORT ({0}) difere de FrontendPort ({1}). O start usa FrontendPort." -f $vitePort, $cfg.FrontendPort) -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Conclusao: start do frontend deployado usa --port FrontendPort (env.config.json), nao VITE_DEV_SERVER_PORT."
Write-Host ("Para mudar a porta: edite Portas no ops-console /env/{0} ou ops/config/env.config.json e aplique." -f $Environment)
exit 0
