. (Join-Path $PSScriptRoot "deploy_paths.ps1")
. (Join-Path $PSScriptRoot "junction.ps1")
. (Join-Path $PSScriptRoot "env_spec.ps1")

function Get-PplidSharedEnvPaths {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment
    )

    $shared = (Get-PplidDeployEnvPaths -Environment $Environment).Shared
    return [PSCustomObject]@{
        Shared   = $shared
        Backend  = Join-Path $shared "backend.env"
        Frontend = Join-Path $shared "frontend.env"
        Media    = Join-Path $shared "media"
    }
}

function Get-PplidSharedMediaDir {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment
    )
    (Get-PplidSharedEnvPaths -Environment $Environment).Media
}

function Test-PplidPathIsReparsePoint {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path $Path)) { return $false }
    $item = Get-Item $Path -Force
    return [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
}

function Test-PplidDirectoryHasFiles {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path $Path)) { return $false }
    return [bool](Get-ChildItem $Path -Recurse -File -Force -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Ensure-PplidEnvKey {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter(Mandatory = $true)][string]$Value
    )

    $lines = @()
    if (Test-Path $FilePath) {
        $lines = Get-Content $FilePath -Encoding UTF8
    }
    $found = $false
    $out = foreach ($line in $lines) {
        if ($line -match "^\s*$([regex]::Escape($Key))\s*=") {
            $found = $true
            "$Key=$Value"
        } else {
            $line
        }
    }
    if (-not $found) {
        $out = @($out) + "$Key=$Value"
    }
    $content = ($out -join "`n") + "`n"
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($FilePath, $content, $utf8NoBom)
}

function Copy-PplidEnvFile {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    $destDir = Split-Path $Destination -Parent
    if (-not (Test-Path $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }
    Copy-Item $Source $Destination -Force
}

function Seed-PplidSharedEnvIfMissing {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,
        [string]$RepoDir = ""
    )

    Initialize-PplidDeployLayout -Environment $Environment
    $shared = Get-PplidSharedEnvPaths -Environment $Environment
    $paths = Get-PplidDeployEnvPaths -Environment $Environment
    if (-not $RepoDir) {
        $spec = Get-PplidEnvSpec -Environment $Environment
        $RepoDir = $spec.RepoDir
    }

    $mediaRootValue = $shared.Media

    if (-not (Test-Path $shared.Backend)) {
        $candidates = @(
            (Join-Path $paths.Current "backend\.env"),
            (Join-Path $RepoDir "backend\.env"),
            (Join-Path $RepoDir "backend\.env.example")
        )
        $seeded = $false
        foreach ($src in $candidates) {
            if (Test-Path $src) {
                Copy-PplidEnvFile -Source $src -Destination $shared.Backend
                $seeded = $true
                break
            }
        }
        if (-not $seeded) {
            $defaults = @(
                "SECRET_KEY=dev-secret-key-change-in-production",
                "DEBUG=True",
                "ALLOWED_HOSTS=localhost,127.0.0.1",
                "POSTGRES_DB=pplid_$($Environment.ToLower())",
                "POSTGRES_USER=postgres",
                "POSTGRES_PASSWORD=postgres",
                "POSTGRES_HOST=localhost",
                "POSTGRES_PORT=5432",
                "SESSION_COOKIE_NAME=pplid_$($Environment.ToLower())_sessionid"
            ) -join "`n"
            $utf8NoBom = New-Object System.Text.UTF8Encoding $false
            [System.IO.File]::WriteAllText($shared.Backend, $defaults + "`n", $utf8NoBom)
        }
    }
    Ensure-PplidEnvKey -FilePath $shared.Backend -Key "MEDIA_ROOT" -Value $mediaRootValue

    if (-not (Test-Path $shared.Frontend)) {
        $feCandidates = @(
            (Join-Path $paths.Current "frontend\.env"),
            (Join-Path $RepoDir "frontend\.env")
        )
        $feSeeded = $false
        foreach ($src in $feCandidates) {
            if (Test-Path $src) {
                Copy-PplidEnvFile -Source $src -Destination $shared.Frontend
                $feSeeded = $true
                break
            }
        }
        if (-not $feSeeded) {
            $spec = Get-PplidEnvSpec -Environment $Environment
            $feLines = @(
                "VITE_API_BASE_URL=",
                "VITE_DEV_SERVER_PORT=$($spec.FrontendPort)",
                "VITE_BACKEND_PORT=$($spec.BackendPort)",
                "VITE_BACKEND_PROXY_TARGET=http://localhost:$($spec.BackendPort)"
            ) -join "`n"
            $utf8NoBom = New-Object System.Text.UTF8Encoding $false
            [System.IO.File]::WriteAllText($shared.Frontend, $feLines + "`n", $utf8NoBom)
        }
    }
}

function Install-PplidSharedEnv {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,
        [Parameter(Mandatory = $true)]
        [string]$AppRoot,
        [string]$RepoDir = ""
    )

    Seed-PplidSharedEnvIfMissing -Environment $Environment -RepoDir $RepoDir
    $shared = Get-PplidSharedEnvPaths -Environment $Environment

    $backendDest = Join-Path $AppRoot "backend\.env"
    $frontendDest = Join-Path $AppRoot "frontend\.env"
    Copy-PplidEnvFile -Source $shared.Backend -Destination $backendDest
    Copy-PplidEnvFile -Source $shared.Frontend -Destination $frontendDest
}

function Find-PplidMediaSeedSource {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,
        [string]$RepoDir = ""
    )

    $paths = Get-PplidDeployEnvPaths -Environment $Environment
    if (-not $RepoDir) {
        $spec = Get-PplidEnvSpec -Environment $Environment
        $RepoDir = $spec.RepoDir
    }

    $candidates = [System.Collections.Generic.List[string]]::new()
    $currentMedia = Join-Path $paths.Current "backend\media"
    if ((Test-Path $currentMedia) -and -not (Test-PplidPathIsReparsePoint $currentMedia)) {
        $candidates.Add($currentMedia)
    }

    if (Test-Path $paths.Releases) {
        Get-ChildItem $paths.Releases -Directory -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            ForEach-Object {
                $m = Join-Path $_.FullName "backend\media"
                if ((Test-Path $m) -and -not (Test-PplidPathIsReparsePoint $m) -and (Test-PplidDirectoryHasFiles $m)) {
                    $candidates.Add($m)
                }
            }
    }

    $repoMedia = Join-Path $RepoDir "backend\media"
    if ((Test-Path $repoMedia) -and (Test-PplidDirectoryHasFiles $repoMedia)) {
        $candidates.Add($repoMedia)
    }

    foreach ($c in $candidates) {
        if (Test-PplidDirectoryHasFiles $c) {
            return $c
        }
    }
    return $null
}

function Seed-PplidSharedMediaIfEmpty {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,
        [string]$RepoDir = ""
    )

    Initialize-PplidDeployLayout -Environment $Environment
    $mediaDir = Get-PplidSharedMediaDir -Environment $Environment
    if (-not (Test-Path $mediaDir)) {
        New-Item -ItemType Directory -Path $mediaDir -Force | Out-Null
    }

    if (Test-PplidDirectoryHasFiles $mediaDir) {
        return
    }

    $source = Find-PplidMediaSeedSource -Environment $Environment -RepoDir $RepoDir
    if (-not $source) {
        return
    }

    Copy-Item -Path (Join-Path $source "*") -Destination $mediaDir -Recurse -Force -ErrorAction SilentlyContinue
}

function Install-PplidSharedMedia {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,
        [Parameter(Mandatory = $true)]
        [string]$AppRoot,
        [string]$RepoDir = ""
    )

    Seed-PplidSharedMediaIfEmpty -Environment $Environment -RepoDir $RepoDir
    $mediaDir = Get-PplidSharedMediaDir -Environment $Environment
    if (-not (Test-Path $mediaDir)) {
        New-Item -ItemType Directory -Path $mediaDir -Force | Out-Null
    }

    $linkPath = Join-Path $AppRoot "backend\media"
    $backendDir = Join-Path $AppRoot "backend"
    if (-not (Test-Path $backendDir)) {
        New-Item -ItemType Directory -Path $backendDir -Force | Out-Null
    }

    if (Test-Path $linkPath) {
        if (Test-PplidPathIsReparsePoint $linkPath) {
            $existing = (Get-Item $linkPath -Force).Target
            $existingStr = if ($existing -is [array]) { $existing[0] } else { [string]$existing }
            $resolvedShared = (Resolve-Path $mediaDir).Path
            if ($existingStr -and ((Resolve-Path $existingStr -ErrorAction SilentlyContinue).Path -eq $resolvedShared)) {
                return
            }
            cmd /c rmdir "$linkPath" 2>$null | Out-Null
        } else {
            if (Test-PplidDirectoryHasFiles $linkPath) {
                Copy-Item -Path (Join-Path $linkPath "*") -Destination $mediaDir -Recurse -Force -ErrorAction SilentlyContinue
            }
            Remove-Item $linkPath -Recurse -Force
        }
    }

    Set-DirectoryJunction -LinkPath $linkPath -TargetPath $mediaDir
}

function Install-PplidSharedRuntime {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,
        [Parameter(Mandatory = $true)]
        [string]$AppRoot,
        [string]$RepoDir = ""
    )

    Install-PplidSharedEnv -Environment $Environment -AppRoot $AppRoot -RepoDir $RepoDir
    Install-PplidSharedMedia -Environment $Environment -AppRoot $AppRoot -RepoDir $RepoDir
}
