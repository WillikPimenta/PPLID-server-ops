. (Join-Path $PSScriptRoot "deploy_state.ps1")
. (Join-Path $PSScriptRoot "legacy_deploy_status.ps1")

$script:PplidEnvMutexes = @{}
$script:PplidEnvMutexOwned = @{}

function Write-PplidDeployLockLog {
    param(
        [string]$Message,
        [string]$Environment = ""
    )

    $logDir = (Get-PplidDeployEnvPaths -Environment $(if ($Environment) { $Environment } else { "DEV" })).Logs
    if (-not $Environment) {
        $logDir = Join-Path (Get-PplidDeployRoot) "logs"
    }
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    $logFile = Join-Path $logDir "deploy-lock.log"
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logFile -Value "[$timestamp] [$Environment] $Message" -Encoding UTF8
}

function Get-PplidDeployMutexName {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment
    )
    return "Global\PPLID-Deploy-$Environment"
}

function Enter-DeployLock {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,
        [int]$MaxDeployMinutes = 45
    )

    $state = Get-DeployState -Environment $Environment

    if ($state.status -in @("building", "validating", "promoting") -and -not $state.lockHolder -and $state.updatedAt) {
        try {
            $zombieMinutes = ([datetime]::Now - [datetime]::Parse([string]$state.updatedAt)).TotalMinutes
            if ($zombieMinutes -ge 5) {
                Write-PplidDeployLockLog -Environment $Environment -Message "Zombie deploy state ($($state.status), no lock, ${zombieMinutes}m), resetting."
                $preservedSha = Get-PplidPreservedActiveSha -Environment $Environment -State $state
                $updates = @{
                    status     = if ($preservedSha) { "idle" } else { "failed" }
                    lastError  = "zombie_timeout"
                    finishedAt = (Get-Date).ToString("o")
                    targetSha  = $null
                    runId      = $null
                    startedAt  = $null
                    lockHolder = $null
                }
                if ($preservedSha) {
                    $updates.activeSha = $preservedSha
                    $updates.lastGoodSha = $preservedSha
                }
                Set-DeployState -Environment $Environment -Updates $updates
                Reset-PplidLegacyDeployPhase -Environment $Environment -Message "zombie_timeout"
                $state = Get-DeployState -Environment $Environment
            }
        } catch {
            Write-PplidDeployLockLog -Environment $Environment -Message "Zombie state check failed: $($_.Exception.Message)"
        }
    }

    if (Test-DeployStateStale -State $state -MaxDeployMinutes $MaxDeployMinutes) {
        Write-PplidDeployLockLog -Environment $Environment -Message "Stale deploy state ($($state.status)), resetting to failed."
        $preservedSha = Get-PplidPreservedActiveSha -Environment $Environment -State $state
        $updates = @{
            status     = if ($preservedSha) { "idle" } else { "failed" }
            lastError  = "stale_timeout"
            finishedAt = (Get-Date).ToString("o")
            targetSha  = $null
            runId      = $null
            startedAt  = $null
        }
        if ($preservedSha) {
            $updates.activeSha = $preservedSha
            $updates.lastGoodSha = $preservedSha
        }
        Set-DeployState -Environment $Environment -Updates $updates
        Reset-PplidLegacyDeployPhase -Environment $Environment -Message "stale_timeout"
    } elseif ($state.status -in @("building", "validating", "promoting")) {
        Write-PplidDeployLockLog -Environment $Environment -Message "Deploy busy ($($state.status)), skip."
        return $false
    }

    if ($script:PplidEnvMutexOwned[$Environment]) {
        return $true
    }

    $name = Get-PplidDeployMutexName -Environment $Environment
    $mutex = New-Object System.Threading.Mutex($false, $name)
    $owned = $mutex.WaitOne(0)
    if (-not $owned) {
        Write-PplidDeployLockLog -Environment $Environment -Message "Mutex held, skip."
        $mutex.Dispose()
        return $false
    }

    $script:PplidEnvMutexes[$Environment] = $mutex
    $script:PplidEnvMutexOwned[$Environment] = $true
    Set-DeployState -Environment $Environment -Updates @{
        lockHolder = "$env:USERNAME@$env:COMPUTERNAME"
    }
    Write-PplidDeployLockLog -Environment $Environment -Message "Lock acquired."
    return $true
}

function Exit-DeployLock {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment
    )

    if (-not $script:PplidEnvMutexOwned[$Environment]) {
        return
    }

    try {
        $script:PplidEnvMutexes[$Environment].ReleaseMutex()
        Write-PplidDeployLockLog -Environment $Environment -Message "Lock released."
    } finally {
        $script:PplidEnvMutexes[$Environment].Dispose()
        $script:PplidEnvMutexes.Remove($Environment) | Out-Null
        $script:PplidEnvMutexOwned[$Environment] = $false
        Set-DeployState -Environment $Environment -Updates @{
            lockHolder = $null
        }
    }
}

function Enter-PplidOrchestratorLock {
    $name = "Global\PPLID-Deploy-Orchestrator"
    $script:PplidOrchestratorMutex = New-Object System.Threading.Mutex($false, $name)
    $script:PplidOrchestratorOwned = $script:PplidOrchestratorMutex.WaitOne(0)
    return $script:PplidOrchestratorOwned
}

function Exit-PplidOrchestratorLock {
    if (-not $script:PplidOrchestratorOwned) { return }
    try {
        $script:PplidOrchestratorMutex.ReleaseMutex()
    } finally {
        $script:PplidOrchestratorMutex.Dispose()
        $script:PplidOrchestratorOwned = $false
    }
}
