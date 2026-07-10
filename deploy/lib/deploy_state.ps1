. (Join-Path $PSScriptRoot "deploy_paths.ps1")

$script:PplidDeployStateLock = New-Object PSCustomObject

function Get-DefaultDeployState {
    return [ordered]@{
        activeSha      = $null
        targetSha      = $null
        previousSha    = $null
        lastGoodSha    = $null
        status         = "idle"
        runId          = $null
        startedAt      = $null
        finishedAt     = $null
        lockHolder     = $null
        lastError      = $null
        blockedSha     = $null
        blockedAt      = $null
        blockedReason  = $null
        blockedRunId   = $null
        updatedAt      = (Get-Date).ToString("o")
    }
}

function Clear-DeployBlockedSha {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment
    )

    return Set-DeployState -Environment $Environment -Updates @{
        blockedSha    = $null
        blockedAt     = $null
        blockedReason = $null
        blockedRunId  = $null
    }
}

function Get-DeployState {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment
    )

    Initialize-PplidDeployLayout -Environment $Environment
    $stateFile = (Get-PplidDeployEnvPaths -Environment $Environment).StateFile
    if (-not (Test-Path $stateFile)) {
        return (Get-DefaultDeployState)
    }

    try {
        $rawText = $null
        for ($attempt = 0; $attempt -lt 5; $attempt++) {
            if (-not (Test-Path $stateFile)) {
                return (Get-DefaultDeployState)
            }
            $rawText = [System.IO.File]::ReadAllText($stateFile, (New-Object System.Text.UTF8Encoding $false))
            if ($rawText.Trim().Length -gt 0) {
                break
            }
            Start-Sleep -Milliseconds 30
        }
        $raw = $rawText | ConvertFrom-Json
        $state = Get-DefaultDeployState
        foreach ($key in @($state.Keys)) {
            if ($null -ne $raw.PSObject.Properties[$key]) {
                $state[$key] = $raw.$key
            }
        }
        return $state
    } catch {
        Write-Warning "deploy-state.json invalido para $Environment, usando defaults parciais."
        return (Get-DefaultDeployState)
    }
}

function Save-DeployState {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,
        [Parameter(Mandatory = $true)]
        [hashtable]$State
    )

    $stateFile = (Get-PplidDeployEnvPaths -Environment $Environment).StateFile
    $State.updatedAt = (Get-Date).ToString("o")
    $json = $State | ConvertTo-Json -Depth 5
    $utf8 = New-Object System.Text.UTF8Encoding $false
    $tmpFile = "$stateFile.tmp"
    [System.IO.File]::WriteAllText($tmpFile, $json, $utf8)
    if (Test-Path $stateFile) {
        [System.IO.File]::Replace($tmpFile, $stateFile, "$stateFile.bak")
        if (Test-Path "$stateFile.bak") {
            Remove-Item -LiteralPath "$stateFile.bak" -Force -ErrorAction SilentlyContinue
        }
    } else {
        Move-Item -LiteralPath $tmpFile -Destination $stateFile -Force
    }
}

function Set-DeployState {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,
        [Parameter(Mandatory = $true)]
        [hashtable]$Updates
    )

    $state = Get-DeployState -Environment $Environment
    foreach ($key in $Updates.Keys) {
        if (($null -eq $Updates[$key] -or [string]::IsNullOrWhiteSpace([string]$Updates[$key])) -and
            $key -in @("activeSha", "lastGoodSha")) {
            continue
        }
        $state[$key] = $Updates[$key]
    }
    if (-not $state.activeSha -and $state.lastGoodSha) {
        $state.activeSha = $state.lastGoodSha
    }
    Save-DeployState -Environment $Environment -State $state
    return $state
}

function Test-DeployStateStale {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,
        [int]$MaxDeployMinutes = 45
    )

    $busy = @("building", "validating", "promoting", "watching")
    if ($State.status -notin $busy) {
        return $false
    }
    if (-not $State.startedAt) {
        return $true
    }

    try {
        $started = [datetime]::Parse($State.startedAt)
        return ((Get-Date) - $started).TotalMinutes -gt $MaxDeployMinutes
    } catch {
        return $true
    }
}

function Sync-DeployStateFromLegacy {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment
    )

    . (Join-Path (Split-Path $PSScriptRoot -Parent) "..\lib\version_drift.ps1")
    $state = Get-DeployState -Environment $Environment
    if ($state.activeSha) {
        return $state
    }

    $currentSha = Get-PplidCurrentReleaseSha -Environment $Environment
    if ($currentSha) {
        Set-DeployState -Environment $Environment -Updates @{
            activeSha   = $currentSha
            lastGoodSha = $currentSha
            status      = "idle"
        }
        return (Get-DeployState -Environment $Environment)
    }

    $deployed = Get-PplidDeployedSha -Environment $Environment
    if ($deployed) {
        Set-DeployState -Environment $Environment -Updates @{
            activeSha   = $deployed
            lastGoodSha = $deployed
            status      = "idle"
        }
    }
    return (Get-DeployState -Environment $Environment)
}
