param(
    [string]$RepoDir = "",
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\paths.ps1")

if (-not $RepoDir) {
    $RepoDir = Get-PplidRepoDir -Name "PPLID_DEV"
}

$OpsDir = Join-Path $RepoDir "ops-console"
$ServerScript = Join-Path $OpsDir "server.py"
$ConfigPath = Join-Path $RepoDir "scripts\deploy\env.config.json"

if (-not (Test-Path $ServerScript)) {
    throw "Ops console nao encontrado: $ServerScript"
}

if (-not (Test-Path $ConfigPath)) {
    throw "Config nao encontrada: $ConfigPath"
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    throw "Python nao encontrado no PATH."
}

$consolePort = 5190
if ($Port -gt 0) {
    $consolePort = $Port
} else {
    try {
        $cfg = Get-Content $ConfigPath -Raw | ConvertFrom-Json
        if ($cfg.opsConsolePort) {
            $consolePort = [int]$cfg.opsConsolePort
        }
    } catch {
        # usa default
    }
}

$existing = Get-NetTCPConnection -LocalPort $consolePort -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Ops Console ja esta escutando na porta $consolePort (PID $($existing[0].OwningProcess))."
    exit 0
}

$args = @($ServerScript, $ConfigPath)
if ($Port -gt 0) {
    $args += $Port
}

Write-Host "Iniciando Ops Console..."
Write-Host "Diretorio: $OpsDir"
Write-Host "Config: $ConfigPath"

Push-Location $OpsDir
try {
    Start-Process -FilePath $python.Source -ArgumentList $args -WindowStyle Hidden
    Start-Sleep -Seconds 2

    $listen = Get-NetTCPConnection -LocalPort $consolePort -State Listen -ErrorAction SilentlyContinue
    if ($listen) {
        $machine = Get-PplidMachineConfig
        $lan = if ($machine.lanIp) { $machine.lanIp } elseif ($cfg.lanIp) { $cfg.lanIp } else { Get-LanIPv4 }
        Write-Host "Ops Console ativo: http://${lan}:$consolePort"
    } else {
        Write-Warning "Processo iniciado, mas porta $consolePort ainda nao responde. Verifique logs."
    }
} finally {
    Pop-Location
}
