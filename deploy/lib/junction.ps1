function Set-DirectoryJunction {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LinkPath,
        [Parameter(Mandatory = $true)]
        [string]$TargetPath
    )

    if (-not (Test-Path $TargetPath)) {
        throw "Target nao existe: $TargetPath"
    }

    if (Test-Path $LinkPath) {
        $item = Get-Item $LinkPath -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            cmd /c rmdir "$LinkPath" 2>$null | Out-Null
        } else {
            throw "Caminho existe e nao e junction: $LinkPath"
        }
    }

    $parent = Split-Path $LinkPath -Parent
    if (-not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }

    $result = cmd /c mklink /J "$LinkPath" "$TargetPath" 2>&1
    if (-not (Test-Path $LinkPath)) {
        throw "Falha ao criar junction: $result"
    }
}
