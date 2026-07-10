$script:PplidSyncMutexName = "Global\PPLID-GitHub-Sync"
$script:PplidSyncMutex = $null
$script:PplidSyncMutexOwned = $false

function Write-PplidSyncLockLog {
    param([string]$Message)

    . (Join-Path $PSScriptRoot "paths.ps1")
    $logDir = Get-PplidLogDir
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }

    $logFile = Join-Path $logDir "update_all.log"
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logFile -Value "[$timestamp] $Message" -Encoding UTF8
}

function Enter-PplidSyncLock {
    if ($script:PplidSyncMutexOwned) {
        return $true
    }

    $script:PplidSyncMutex = New-Object System.Threading.Mutex($false, $script:PplidSyncMutexName)
    $script:PplidSyncMutexOwned = $script:PplidSyncMutex.WaitOne(0)

    if (-not $script:PplidSyncMutexOwned) {
        Write-PplidSyncLockLog "Sync ja em execucao, ignorando."
        return $false
    }

    Write-PplidSyncLockLog "Lock adquirido."
    return $true
}

function Exit-PplidSyncLock {
    if (-not $script:PplidSyncMutexOwned) {
        return
    }

    try {
        $script:PplidSyncMutex.ReleaseMutex()
        Write-PplidSyncLockLog "Lock liberado."
    } finally {
        $script:PplidSyncMutex.Dispose()
        $script:PplidSyncMutex = $null
        $script:PplidSyncMutexOwned = $false
    }
}
