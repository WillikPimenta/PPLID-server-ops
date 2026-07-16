param(
    [string]$OpsDir = "",
    [string]$ConfigPath = "",
    [int]$Port = 0,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\paths.ps1")
. (Join-Path $PSScriptRoot "lib\port_utils.ps1")

if (-not $OpsDir) {
    $OpsDir = Get-PplidOpsConsoleDir -ScriptRoot $PSScriptRoot
}

if (-not $ConfigPath) {
    $ConfigPath = Get-PplidEnvConfigPath -ScriptRoot $PSScriptRoot
}

$ServerScript = Join-Path $OpsDir "server.py"

if (-not (Test-Path $ServerScript)) {
    throw "Ops console nao encontrado: $ServerScript"
}

if (-not (Test-Path $ConfigPath)) {
    throw "Config nao encontrada: $ConfigPath"
}

function Resolve-OpsConsolePython {
    param([string]$BaseDir)

    $devRepoDir = Get-PplidRepoDir -Name "PPLID_DEV"
    $candidates = @(
        (Join-Path $BaseDir "deploy\DEV\current\backend\.venv\Scripts\python.exe")
        (Join-Path $devRepoDir "backend\.venv\Scripts\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (-not (Test-Path $candidate)) { continue }
        try {
            $check = & $candidate -c "import psycopg; print('ok')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $check -eq "ok") {
                return $candidate
            }
        } catch { }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }
    throw "Python com psycopg nao encontrado. Instale deps do backend ou use o venv em deploy/DEV/current."
}

$baseDir = Get-PplidBaseDir
$pythonExe = Resolve-OpsConsolePython -BaseDir $baseDir

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

$existingPid = Get-PortListenOwnerPid -Port $consolePort
if ($existingPid) {
    if ($Restart) {
        Write-Host "Reiniciando Ops Console (PID $existingPid) na porta $consolePort..."
        try {
            Stop-Process -Id $existingPid -Force -ErrorAction Stop
            Start-Sleep -Seconds 1
        } catch {
            Write-Warning "Nao foi possivel encerrar PID ${existingPid}: $_"
        }
        $existingPid = Get-PortListenOwnerPid -Port $consolePort
        if ($existingPid) {
            throw "Porta $consolePort ainda em uso (PID $existingPid) apos tentativa de restart."
        }
    } else {
        Write-Host "Ops Console ja esta escutando na porta $consolePort (PID $existingPid)."
        Write-Host "Use -Restart para reiniciar e aplicar codigo atualizado."
        exit 0
    }
}

$args = @($ServerScript, $ConfigPath)
if ($Port -gt 0) {
    $args += $Port
}

Write-Host "Iniciando Ops Console..."
Write-Host "Python: $pythonExe"
Write-Host "Diretorio: $OpsDir"
Write-Host "Config: $ConfigPath"

Push-Location $OpsDir
try {
    Start-Process -FilePath $pythonExe -ArgumentList $args -WindowStyle Hidden
    Start-Sleep -Seconds 2

    if (Test-PortListening -Port $consolePort) {
        $machine = Get-PplidMachineConfig
        $lan = if ($machine.lanIp) { $machine.lanIp } elseif ($cfg.lanIp) { $cfg.lanIp } else { Get-LanIPv4 }
        Write-Host "Ops Console ativo: http://${lan}:$consolePort"
    } else {
        Write-Warning "Processo iniciado, mas porta $consolePort ainda nao responde. Verifique logs."
    }
} finally {
    Pop-Location
}
exit 0
