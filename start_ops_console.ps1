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

    $venvDir = Join-Path $OpsDir ".venv"
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    $requirements = Join-Path $OpsDir "requirements.txt"
    if (-not (Test-Path $venvPython)) {
        $systemPython = Get-Command python -ErrorAction SilentlyContinue
        if (-not $systemPython) {
            throw "Python nao encontrado para criar o ambiente do Ops Console."
        }
        Write-Host "Criando ambiente Python proprio do Ops Console..."
        & $systemPython.Source -m venv $venvDir
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
            throw "Falha ao criar ambiente Python em $venvDir"
        }
    }

    $dependencyCheck = ""
    $dependencyExitCode = 1
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        $dependencyCheck = & $venvPython -c "import psycopg, psutil; print('ok')" 2>$null
        $dependencyExitCode = $LASTEXITCODE
    } catch {
        $dependencyExitCode = 1
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($dependencyExitCode -ne 0 -or $dependencyCheck -ne "ok") {
        if (-not (Test-Path $requirements)) {
            throw "Dependencias do Ops Console nao encontradas: $requirements"
        }
        Write-Host "Instalando dependencias do Ops Console..."
        & $venvPython -m pip install --disable-pip-version-check -r $requirements
        if ($LASTEXITCODE -ne 0) {
            throw "Falha ao instalar dependencias do Ops Console."
        }
    }
    return $venvPython
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
            Start-Sleep -Milliseconds 500
        } catch {
            Write-Warning "Nao foi possivel encerrar PID ${existingPid}: $_"
        }
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            $existingPid = Get-PortListenOwnerPid -Port $consolePort
            if (-not $existingPid -or -not (Get-Process -Id $existingPid -ErrorAction SilentlyContinue)) {
                $existingPid = $null
                break
            }
            Start-Sleep -Milliseconds 250
        }
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
