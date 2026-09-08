function Test-PplidPathIsReparsePoint {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $item = Get-Item -LiteralPath $Path -Force
    return [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
}

function Test-PplidDirectoryLooksLikeReleaseTree {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return $false }
    $markers = @(
        (Join-Path $Path "backend\manage.py"),
        (Join-Path $Path "backend\.venv"),
        (Join-Path $Path "frontend\dist"),
        (Join-Path $Path "frontend\package.json")
    )
    foreach ($marker in $markers) {
        if (Test-Path -LiteralPath $marker) { return $true }
    }
    return $false
}

function Remove-PplidJunctionOrResidualPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$AllowResidualDirectory
    )

    if (-not (Test-Path -LiteralPath $Path)) { return }

    $item = Get-Item -LiteralPath $Path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        cmd /c rmdir "$Path" 2>$null | Out-Null
        if (Test-Path -LiteralPath $Path) {
            throw "Falha ao remover junction existente: $Path"
        }
        return
    }

    if (-not $item.PSIsContainer) {
        throw "Caminho existe e nao e diretorio/junction: $Path"
    }

    if (-not $AllowResidualDirectory) {
        throw "Caminho existe e nao e junction: $Path"
    }

    if (Test-PplidDirectoryLooksLikeReleaseTree -Path $Path) {
        throw "Caminho existe como diretorio de release (nao junction) e nao pode ser substituido automaticamente: $Path. Remova manualmente apos confirmar que nao ha dados unicos."
    }

    Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
    if (Test-Path -LiteralPath $Path) {
        throw "Falha ao remover diretorio residual: $Path"
    }
}

function Set-DirectoryJunction {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LinkPath,
        [Parameter(Mandatory = $true)]
        [string]$TargetPath,
        [switch]$AllowReplaceResidualDirectory
    )

    if (-not (Test-Path -LiteralPath $TargetPath)) {
        throw "Target nao existe: $TargetPath"
    }

    $resolvedTarget = (Resolve-Path -LiteralPath $TargetPath).Path
    $allowResidual = $AllowReplaceResidualDirectory -or ($LinkPath -match '(?i)[\\/](current|previous)$')

    Remove-PplidJunctionOrResidualPath -Path $LinkPath -AllowResidualDirectory:$allowResidual

    $parent = Split-Path $LinkPath -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }

    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = cmd /c mklink /J "$LinkPath" "$resolvedTarget" 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }

    $outputText = (@($output) | ForEach-Object { "$_" }) -join " "
    if ($code -ne 0 -or -not (Test-Path -LiteralPath $LinkPath)) {
        throw "Falha ao criar junction (exit $code): $LinkPath -> $resolvedTarget. $outputText"
    }

    $created = Get-Item -LiteralPath $LinkPath -Force
    if (-not ($created.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Caminho criado nao e junction: $LinkPath"
    }
}
