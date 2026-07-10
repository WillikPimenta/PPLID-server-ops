$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\paths.ps1")
. (Join-Path $PSScriptRoot "lib\port_utils.ps1")

Write-Host "=== PPLID Stop All ==="

foreach ($env in @("MAIN", "DEV", "HOM")) {
    $repoName = "PPLID_$env"
    $repoDir = Get-PplidRepoDir -Name $repoName
    $stopScript = Join-Path $repoDir "scripts\deploy\stop_env.ps1"

    Write-Host "--- Stop $env ---"
    if (-not (Test-Path $stopScript)) {
        Write-Warning "Script nao encontrado: $stopScript"
        continue
    }

    & powershell -ExecutionPolicy Bypass -File $stopScript -Environment $env
}

$configPath = Get-PplidEnvConfigPath -ScriptRoot $PSScriptRoot
$consolePort = 5190
if (Test-Path $configPath) {
    try {
        $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
        if ($cfg.opsConsolePort) {
            $consolePort = [int]$cfg.opsConsolePort
        }
    } catch {
        # usa default
    }
}

Write-Host "--- Stop Ops Console (porta $consolePort) ---"
$killed = Stop-PortListeners -Port $consolePort
if ($killed.Count -gt 0) {
    Write-Host "Ops Console encerrado (PIDs: $($killed -join ', '))"
} elseif (Test-PortListening -Port $consolePort) {
    Write-Warning "Porta $consolePort ainda ativa (processo de outro usuario/SYSTEM - requer admin ou reinicio)"
} else {
    Write-Host "Porta $consolePort : nenhum processo ativo"
}

Write-Host ""
Write-Host "Portas restantes (Listen):"
$ports = @(8000, 8001, 8002, 5173, 5174, 5175, $consolePort)
foreach ($port in $ports) {
    if (Test-PortListening -Port $port) {
        $owner = Get-PortListenOwnerPid -Port $port
        Write-Host "  TCP $port : PID $owner (nao encerrado)"
    }
}

Write-Host ""
Write-Host "Stop concluido."
exit 0
