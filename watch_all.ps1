$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\paths.ps1")
. (Join-Path $PSScriptRoot "lib\sync_lock.ps1")
. (Join-Path $PSScriptRoot "deploy\lib\deploy_lock.ps1")

Initialize-PplidGitSafeDirectories

if (-not (Enter-PplidSyncLock)) {
    exit 0
}

try {
    $enabledEnvs = @(Get-PplidEnabledEnvironments -ScriptRoot $PSScriptRoot)
    if ($enabledEnvs.Count -eq 0) {
        Write-Host "Nenhum ambiente habilitado em env.config.json; sync ignorado."
        exit 0
    }
    foreach ($env in $enabledEnvs) {
        Write-Host "=== Watch $env ==="
        & (Join-Path $PSScriptRoot "deploy\watch_github.ps1") -Environment $env
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Watch $env retornou exit $LASTEXITCODE"
        }
    }
} finally {
    Exit-PplidSyncLock
}
