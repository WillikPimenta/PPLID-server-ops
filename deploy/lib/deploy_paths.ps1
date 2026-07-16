. (Join-Path (Split-Path $PSScriptRoot -Parent) "..\lib\paths.ps1")

function Get-PplidDeployRoot {
    Join-Path (Get-PplidBaseDir) "deploy"
}

function Get-PplidPipCacheDir {
    Join-Path (Get-PplidDeployRoot) "cache\pip"
}

function Get-PplidDeployEnvRoot {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment
    )
    Join-Path (Get-PplidDeployRoot) $Environment
}

function Get-PplidDeployEnvPaths {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment
    )

    $root = Get-PplidDeployEnvRoot -Environment $Environment
    return [PSCustomObject]@{
        Root                 = $root
        Mirror               = Join-Path $root "mirror"
        Staging              = Join-Path $root "staging"
        Releases             = Join-Path $root "releases"
        Current              = Join-Path $root "current"
        Previous             = Join-Path $root "previous"
        Logs                 = Join-Path $root "logs"
        Runs                 = Join-Path $root "logs\runs"
        StateFile            = Join-Path $root "deploy-state.json"
        CancelRequestedFile  = Join-Path $root "cancel-requested.json"
    }
}

function Get-PplidDeployRunDir {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,
        [Parameter(Mandatory = $true)]
        [string]$RunId
    )
    Join-Path (Get-PplidDeployEnvPaths -Environment $Environment).Runs $RunId
}

function Initialize-PplidDeployLayout {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment
    )

    $paths = Get-PplidDeployEnvPaths -Environment $Environment
    $pipCache = Get-PplidPipCacheDir
    foreach ($dir in @($paths.Root, $paths.Mirror, $paths.Staging, $paths.Releases, $paths.Logs, $paths.Runs, $pipCache)) {
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
    }
}

function Get-PplidReleaseDir {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,
        [Parameter(Mandatory = $true)]
        [string]$Sha
    )
    Join-Path (Get-PplidDeployEnvPaths -Environment $Environment).Releases $Sha
}

function Get-PplidCurrentReleaseSha {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment
    )

    $metaFile = Join-Path (Get-PplidDeployEnvPaths -Environment $Environment).Current "meta.json"
    if (-not (Test-Path $metaFile)) {
        return $null
    }

    try {
        $meta = Get-Content $metaFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $sha = [string]$meta.sha
        if ($sha) {
            return $sha.Trim()
        }
    } catch {
        return $null
    }

    return $null
}

function Resolve-PplidAppRoot {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,
        [string]$FallbackRepoDir = ""
    )

    $current = (Get-PplidDeployEnvPaths -Environment $Environment).Current
    $backend = Join-Path $current "backend"
    if (Test-Path $backend) {
        return $current
    }
    if ($FallbackRepoDir -and (Test-Path (Join-Path $FallbackRepoDir "backend"))) {
        return $FallbackRepoDir
    }
    return $null
}
