$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\paths.ps1")
. (Join-Path $PSScriptRoot "lib\sync_lock.ps1")
. (Join-Path $PSScriptRoot "deploy\lib\deploy_lock.ps1")

Initialize-PplidGitSafeDirectories

if (-not (Enter-PplidSyncLock)) {
    exit 0
}

try {
    foreach ($env in @("MAIN", "DEV", "HOM")) {
        Write-Host "=== Watch $env ==="
        & (Join-Path $PSScriptRoot "deploy\watch_github.ps1") -Environment $env
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Watch $env retornou exit $LASTEXITCODE"
        }
    }
} finally {
    Exit-PplidSyncLock
}
