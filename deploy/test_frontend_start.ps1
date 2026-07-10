param(
    [ValidateSet("MAIN", "DEV", "HOM")]
    [string]$TargetEnvironment = "HOM",
    [int]$TestPort = 55999
)

$ErrorActionPreference = "Stop"
$opsRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $opsRoot "lib\paths.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_paths.ps1")
. (Join-Path $PSScriptRoot "lib\env_spec.ps1")

$envSpec = Get-PplidEnvSpec -Environment $TargetEnvironment
$libPath = Join-Path $envSpec.RepoDir "scripts\deploy\lib.ps1"
if (-not (Test-Path $libPath)) {
    throw "lib.ps1 nao encontrado: $libPath"
}

. $libPath

$paths = Get-PplidRuntimePaths -Environment $TargetEnvironment
$config = $paths.Config

if (-not (Test-Path $paths.ViteBin)) {
    throw "vite local ausente: $($paths.ViteBin)"
}

$frontendDist = Join-Path $paths.FrontendDir "dist"
if (-not (Test-Path $frontendDist)) {
    throw "Build frontend ausente: $frontendDist"
}

Write-Host "Smoke frontend start ($TargetEnvironment) porta efemera $TestPort..."

Stop-PortProcess -Port $TestPort | Out-Null
Start-Sleep -Milliseconds 500

$viteCmd = $paths.ViteBin -replace '"', '""'
$testLog = Join-Path $env:TEMP "pplid-frontend-smoke-$TestPort.log"
if (Test-Path $testLog) { Remove-Item $testLog -Force }

$proc = Start-Process `
    -FilePath $paths.ViteBin `
    -ArgumentList @("preview", "--port", "$TestPort", "--host", "127.0.0.1") `
    -WorkingDirectory $paths.FrontendDir `
    -RedirectStandardOutput $testLog `
    -RedirectStandardError ($testLog + ".err") `
    -PassThru `
    -WindowStyle Hidden

try {
    if (-not (Wait-FrontendPortListen -Port $TestPort -TimeoutSec 30)) {
        $tail = Get-LogTail -FilePath $testLog -Lines 15
        throw "Frontend nao escutou na porta $TestPort em 30s. Log: $tail"
    }

    $response = Invoke-WebRequest -Uri "http://127.0.0.1:$TestPort/" -UseBasicParsing -TimeoutSec 5
    if ($response.StatusCode -ge 500) {
        throw "HTTP $($response.StatusCode) em http://127.0.0.1:$TestPort/"
    }

    Write-Host "[OK] Frontend preview respondeu na porta $TestPort (PID $($proc.Id))"
} finally {
    Stop-PortProcess -Port $TestPort | Out-Null
    if (-not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path $testLog) { Remove-Item $testLog -Force -ErrorAction SilentlyContinue }
}

Write-Host "Smoke test frontend start: $TargetEnvironment OK"
