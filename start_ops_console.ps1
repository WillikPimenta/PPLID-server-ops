param(
    [string]$OpsDir = "",
    [string]$ConfigPath = "",
    [int]$Port = 0,
    [switch]$Restart,
    [switch]$Local,
    [string]$PplidDir = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\paths.ps1")
. (Join-Path $PSScriptRoot "lib\port_utils.ps1")

if ($Local) {
    # Local mode is fully self-contained in this checkout and never consults
    # the server's C:\PPLID or ProgramData configuration.
    $localRoot = Join-Path $PSScriptRoot ".local"
    $localConfigDir = Join-Path $PSScriptRoot "config"
    $localMachinePath = Join-Path $localConfigDir "machine.config.local.json"
    $localConfigPath = Join-Path $localConfigDir "env.config.local.json"
    if (-not (Test-Path $localConfigPath)) {
        $example = Join-Path $localConfigDir "env.config.local.example.json"
        if (-not (Test-Path $example)) { throw "Exemplo de configuracao local nao encontrado: $example" }
        Copy-Item $example $localConfigPath
    }
    if (-not (Test-Path $localMachinePath)) {
        if (-not $PplidDir) {
            $siblingPplid = Join-Path (Split-Path $PSScriptRoot -Parent) "PPLID"
            if (Test-Path (Join-Path $siblingPplid "deploy")) { $PplidDir = $siblingPplid }
        }
        $sourceRoot = if ($PplidDir) { (Resolve-Path $PplidDir).Path } else { $localRoot }
        $localMachine = [ordered]@{
            baseDir = $localRoot
            lanIp = "127.0.0.1"
            reposDir = (Join-Path $sourceRoot "repos")
            deployDir = (Join-Path $localRoot "deploy")
            automationSourceDir = if ($PplidDir) { $sourceRoot } else { $null }
            automationOpsDir = (Join-Path $PSScriptRoot "ops-console\automation-native")
            envConfigPath = $localConfigPath
            opsConsoleDir = (Join-Path $PSScriptRoot "ops-console")
            automationRuntime = @{ root = (Join-Path $localRoot "automation-runtime") }
        }
        $localMachine | ConvertTo-Json -Depth 5 | Set-Content -Path $localMachinePath -Encoding UTF8
    }
    if ($PplidDir) {
        $existingMachine = Get-Content $localMachinePath -Raw | ConvertFrom-Json
        $existingMachine | Add-Member -NotePropertyName reposDir -NotePropertyValue (Join-Path $PplidDir "repos") -Force
        $existingMachine | Add-Member -NotePropertyName deployDir -NotePropertyValue (Join-Path $localRoot "deploy") -Force
        $existingMachine | Add-Member -NotePropertyName automationSourceDir -NotePropertyValue ((Resolve-Path $PplidDir).Path) -Force
        $existingMachine | Add-Member -NotePropertyName automationOpsDir -NotePropertyValue (Join-Path $PSScriptRoot "ops-console\automation-native") -Force
        $existingMachine | ConvertTo-Json -Depth 5 | Set-Content -Path $localMachinePath -Encoding UTF8
    }
    foreach ($dir in @("repos", "logs", "deploy", "automation-runtime")) {
        New-Item -ItemType Directory -Path (Join-Path $localRoot $dir) -Force | Out-Null
    }
    $env:OPS_MACHINE_CONFIG = $localMachinePath
    $env:OPS_HOST = "127.0.0.1"
    if (-not $Port) { $Port = 5191 }
}

if (-not $OpsDir) {
    $OpsDir = if ($Local) { Join-Path $PSScriptRoot "ops-console" } else { Get-PplidOpsConsoleDir -ScriptRoot $PSScriptRoot }
}

if (-not $ConfigPath) {
    $ConfigPath = if ($Local) { Join-Path $PSScriptRoot "config\env.config.local.json" } else { Get-PplidEnvConfigPath -ScriptRoot $PSScriptRoot }
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
            if ($Local) {
                Write-Warning "Nao foi possivel criar .venv local; usando o Python do sistema para desenvolvimento."
                $venvPython = $systemPython.Source
            } else {
                throw "Falha ao criar ambiente Python em $venvDir"
            }
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
        if ($Local) {
            $systemPython = Get-Command python -ErrorAction SilentlyContinue
            if ($systemPython) {
                $systemCheck = & $systemPython.Source -c "import psycopg, psutil; print('ok')" 2>$null
                if ($LASTEXITCODE -eq 0 -and $systemCheck -eq "ok") {
                    Write-Warning "Usando o Python do sistema para desenvolvimento local."
                    return $systemPython.Source
                }
            }
        }
        if (-not (Test-Path $requirements)) {
            throw "Dependencias do Ops Console nao encontradas: $requirements"
        }
        Write-Host "Instalando dependencias do Ops Console..."
        & $venvPython -m pip install --disable-pip-version-check -r $requirements
        if ($LASTEXITCODE -ne 0) {
            if ($Local) {
                $systemPython = Get-Command python -ErrorAction SilentlyContinue
                if ($systemPython) {
                    $systemCheck = & $systemPython.Source -c "import psycopg, psutil; print('ok')" 2>$null
                    if ($LASTEXITCODE -eq 0 -and $systemCheck -eq "ok") {
                        Write-Warning "Dependencias nao puderam ser instaladas no .venv; usando o Python do sistema."
                        return $systemPython.Source
                    }
                }
            }
            throw "Falha ao instalar dependencias do Ops Console."
        }
    }
    return $venvPython
}

$baseDir = Get-PplidBaseDir
$pythonExe = Resolve-OpsConsolePython -BaseDir $baseDir

function Ensure-AutomationRuntime {
    param(
        [string]$PythonExe,
        [string]$OpsConsoleDir,
        [string]$EnvConfigPath
    )
    Write-Host "Preparando runtime de automações..."
    $env:OPS_CONFIG = $EnvConfigPath
    $prev = Get-Location
    try {
        Set-Location $OpsConsoleDir
        $bootstrapScript = Join-Path $OpsConsoleDir "tools\bootstrap_automation_runtime.py"
        if (-not (Test-Path $bootstrapScript)) {
            Write-Warning "Script de bootstrap nao encontrado: $bootstrapScript"
            return
        }
        & $PythonExe $bootstrapScript $EnvConfigPath
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Runtime de automações pronto."
        } else {
            Write-Warning "Runtime de automações ainda nao esta pronto (veja a saida acima)."
        }
    } catch {
        Write-Warning "Nao foi possivel preparar o runtime de automacoes agora: $_"
    } finally {
        Set-Location $prev
    }
}

Ensure-AutomationRuntime -PythonExe $pythonExe -OpsConsoleDir $OpsDir -EnvConfigPath $ConfigPath

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
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $pythonExe
    $startInfo.Arguments = ($args | ForEach-Object {
        if ($_ -match '\s') { '"' + $_ + '"' } else { $_ }
    }) -join ' '
    $startInfo.WorkingDirectory = $OpsDir
    $startInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $startInfo.UseShellExecute = $false
    if ($Local -and $localMachinePath) {
        $startInfo.EnvironmentVariables["OPS_MACHINE_CONFIG"] = $localMachinePath
        $startInfo.EnvironmentVariables["OPS_HOST"] = "127.0.0.1"
    }
    [System.Diagnostics.Process]::Start($startInfo) | Out-Null
    Start-Sleep -Seconds 2

    if (Test-PortListening -Port $consolePort) {
        $lan = if ($Local) { "127.0.0.1" } else {
            $machine = Get-PplidMachineConfig
            if ($machine.lanIp) { $machine.lanIp } elseif ($cfg.lanIp) { $cfg.lanIp } else { Get-LanIPv4 }
        }
        Write-Host "Ops Console ativo: http://${lan}:$consolePort"
    } else {
        Write-Warning "Processo iniciado, mas porta $consolePort ainda nao responde. Verifique logs."
    }
} finally {
    Pop-Location
}
exit 0
