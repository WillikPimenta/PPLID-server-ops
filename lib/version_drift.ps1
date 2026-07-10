function Test-PplidShaMatch {
    param(
        [string]$Left,
        [string]$Right
    )

    if (-not $Left -or -not $Right) {
        return $false
    }

    $left = $Left.Trim()
    $right = $Right.Trim()
    return ($left -eq $right) -or $left.StartsWith($right) -or $right.StartsWith($left)
}

function Get-PplidRepoShortSha {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoDir
    )

    Push-Location $RepoDir
    try {
        return (git rev-parse --short HEAD).Trim()
    } finally {
        Pop-Location
    }
}

function Get-PplidDeployStatusEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment
    )

    . (Join-Path $PSScriptRoot "paths.ps1")
    $statusPath = Get-PplidStatusFile
    if (-not (Test-Path $statusPath)) {
        return $null
    }

    try {
        $status = Get-Content $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
        return $status.environments.$Environment
    } catch {
        return $null
    }
}

function Get-PplidDeployedSha {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment
    )

    . (Join-Path (Split-Path $PSScriptRoot -Parent) "deploy\lib\deploy_state.ps1")
    $state = Get-DeployState -Environment $Environment
    if ($state.activeSha) {
        return [string]$state.activeSha.Trim()
    }

    . (Join-Path (Split-Path $PSScriptRoot -Parent) "deploy\lib\deploy_paths.ps1")
    $currentSha = Get-PplidCurrentReleaseSha -Environment $Environment
    if ($currentSha) {
        return $currentSha.Trim()
    }

    $envStatus = Get-PplidDeployStatusEnvironment -Environment $Environment
    if ($envStatus -and $envStatus.deployedSha) {
        return [string]$envStatus.deployedSha
    }

    if ($envStatus -and $envStatus.lastDeployResult -eq "success" -and $envStatus.gitSha) {
        return [string]$envStatus.gitSha
    }

    . (Join-Path $PSScriptRoot "paths.ps1")
    $deployedFile = Join-Path (Get-PplidLogDir) "PPLID_$Environment.deployed.json"
    if (-not (Test-Path $deployedFile)) {
        return $null
    }

    try {
        $payload = Get-Content $deployedFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $sha = [string]$payload.sha
        if ($sha) {
            return $sha.Trim()
        }
    } catch {
        return $null
    }

    return $null
}

function Test-PplidVersionDrift {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,

        [Parameter(Mandatory = $true)]
        [string]$RepoDir
    )

    . (Join-Path (Split-Path $PSScriptRoot -Parent) "deploy\lib\deploy_paths.ps1")
    $releaseVersion = Get-PplidCurrentReleaseSha -Environment $Environment
    if (-not $releaseVersion) {
        $releaseVersion = Get-PplidRepoShortSha -RepoDir $RepoDir
    }
    if (-not $releaseVersion) {
        return $null
    }

    $envStatus = Get-PplidDeployStatusEnvironment -Environment $Environment
    if ($envStatus -and $envStatus.phase -eq "deploying") {
        $started = $envStatus.lastDeployStartedAt
        if ($started) {
            try {
                $age = ((Get-Date) - [datetime]::Parse($started)).TotalMinutes
                if ($age -lt 45) {
                    return $null
                }
            } catch { }
        }
    }

    if ($envStatus -and $envStatus.updatePending) {
        $deployedVersion = Get-PplidDeployedSha -Environment $Environment
        return [PSCustomObject]@{
            repoVersion      = $releaseVersion
            deployedVersion  = if ($deployedVersion) { $deployedVersion } else { "nenhum" }
            updatePending    = $true
            source           = "updatePending"
        }
    }

    $deployedVersion = Get-PplidDeployedSha -Environment $Environment
    if (-not $deployedVersion) {
        return [PSCustomObject]@{
            repoVersion      = $releaseVersion
            deployedVersion  = "nenhum"
            updatePending    = $false
            source           = "missing-deployed"
        }
    }

    if (Test-PplidShaMatch -Left $releaseVersion -Right $deployedVersion) {
        return $null
    }

    return [PSCustomObject]@{
        repoVersion      = $releaseVersion
        deployedVersion  = $deployedVersion
        updatePending    = $false
        source           = "release-vs-deployed"
    }
}
