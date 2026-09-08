$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\paths.ps1")
. (Join-Path $PSScriptRoot "lib\sync_lock.ps1")
. (Join-Path $PSScriptRoot "deploy\lib\deploy_lock.ps1")

$syncWaitSeconds = 120
$syncWaitInterval = 5
$syncAcquired = $false
for ($elapsed = 0; $elapsed -le $syncWaitSeconds; $elapsed += $syncWaitInterval) {
    if (Enter-PplidSyncLock) {
        $syncAcquired = $true
        Exit-PplidSyncLock
        break
    }
    if ($elapsed -lt $syncWaitSeconds) {
        Write-Host "Sync ativo; aguardando ${syncWaitInterval}s antes de tentar bootstrap ($elapsed/${syncWaitSeconds}s)..."
        Start-Sleep -Seconds $syncWaitInterval
    }
}
if (-not $syncAcquired) {
    Write-Host "Sync ainda ativo apos ${syncWaitSeconds}s; bootstrap adiado."
    exit 0
}

if (-not (Enter-PplidOrchestratorLock)) {
    Write-Host "Bootstrap ja em execucao (outro processo segura o lock). Encerre PowerShell preso ou reinicie a sessao."
    exit 1
}

try {
    $enabledEnvs = @(Get-PplidEnabledEnvironments -ScriptRoot $PSScriptRoot)
    if ($enabledEnvs.Count -eq 0) {
        Write-Host "Nenhum ambiente habilitado em env.config.json; bootstrap ignorado."
        exit 0
    }
    & (Join-Path $PSScriptRoot "deploy\init_deploy_layout.ps1") -Environment ALL
    foreach ($env in $enabledEnvs) {
        Write-Host "=== Bootstrap $env ==="
        & (Join-Path $PSScriptRoot "deploy\bootstrap_env.ps1") -Environment $env
    }
    Write-Host "Bootstrap concluido."
} finally {
    Exit-PplidOrchestratorLock
}
