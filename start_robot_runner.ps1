<#
.SYNOPSIS
  Inicia robot_runner em processo independente do Waitress/HTTP.

.DESCRIPTION
  Use este script (Task Scheduler ou manual) para automacao em producao.
  Nao dispare robot_runner a partir do worker HTTP — isso acopla CPU/IO ao MAIN.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\start_robot_runner.ps1 -Environment MAIN -Mode production
#>
param(
    [ValidateSet("MAIN", "DEV", "HOM")]
    [string]$Environment = "MAIN",

    [string]$Mode = "production"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\paths.ps1")
if (-not (Test-PplidEnvironmentEnabled -Environment $Environment -ScriptRoot $PSScriptRoot)) {
    Write-Host "Ambiente $Environment desativado (enabled=false); robot_runner nao iniciado."
    exit 0
}

$base = Get-PplidBaseDir
$release = Join-Path $base "deploy\$Environment\current\automacoes"
if (-not (Test-Path $release)) {
    throw "automacoes nao encontrado em $release"
}

$python = Join-Path $base "deploy\$Environment\current\backend\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

$logDir = Join-Path $base "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$outLog = Join-Path $logDir "PPLID_$Environment.robot_runner.out.log"
$errLog = Join-Path $logDir "PPLID_$Environment.robot_runner.err.log"
$pidFile = Join-Path $logDir "PPLID_$Environment.robot_runner.pid"

if (Test-Path $pidFile) {
    $existing = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($existing) {
        $p = Get-Process -Id ([int]$existing) -ErrorAction SilentlyContinue
        if ($p) {
            Write-Host "robot_runner ja em execucao (PID $existing)"
            exit 0
        }
    }
}

$argList = @("-m", "app.orchestration.robot_runner", "--mode", $Mode)
$proc = Start-Process `
    -FilePath $python `
    -ArgumentList $argList `
    -WorkingDirectory $release `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -PassThru `
    -WindowStyle Hidden

$proc.Id | Out-File -FilePath $pidFile -Encoding ascii
Write-Host "robot_runner iniciado PID=$($proc.Id) mode=$Mode env=$Environment"
Write-Host "logs: $outLog / $errLog"
