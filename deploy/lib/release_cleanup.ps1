. (Join-Path $PSScriptRoot "deploy_paths.ps1")
. (Join-Path $PSScriptRoot "git_invoke.ps1")

function Get-PplidProtectedReleaseShas {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment
    )

    . (Join-Path $PSScriptRoot "deploy_state.ps1")
    $paths = Get-PplidDeployEnvPaths -Environment $Environment
    $protected = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)

    foreach ($linkName in @("Current", "Previous")) {
        $linkPath = $paths.$linkName
        if (-not (Test-Path $linkPath)) { continue }
        try {
            $target = (Get-Item $linkPath -Force).Target
            if ($target -is [Array]) { $target = $target[0] }
            if ($target) {
                [void]$protected.Add((Split-Path $target -Leaf).Trim())
            }
        } catch { }
    }

    $state = Get-DeployState -Environment $Environment
    foreach ($key in @("activeSha", "previousSha", "lastGoodSha", "targetSha")) {
        $sha = [string]$state.$key
        if ($sha) { [void]$protected.Add($sha.Trim()) }
    }

    $busy = @("building", "validating", "promoting", "watching")
    if ($state.status -in $busy -and $state.targetSha) {
        [void]$protected.Add(([string]$state.targetSha).Trim())
    }

    return $protected
}

function Remove-PplidReleaseDir {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,
        [Parameter(Mandatory = $true)]
        [string]$ReleaseDir
    )

    if (-not (Test-Path $ReleaseDir)) { return }

    $paths = Get-PplidDeployEnvPaths -Environment $Environment
    Push-Location $paths.Mirror
    try {
        try {
            Invoke-PplidGit -Args @("worktree", "remove", $ReleaseDir, "--force") -FailMessage "worktree remove falhou."
        } catch {
            Write-Warning "worktree remove: $($_.Exception.Message)"
        }
        Invoke-PplidGit -Args @("worktree", "prune") -FailMessage "worktree prune falhou."
    } finally {
        Pop-Location
    }

    if (Test-Path $ReleaseDir) {
        Remove-Item -LiteralPath $ReleaseDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Get-PplidReleaseSortDate {
    param([string]$ReleaseDir)

    $metaFile = Join-Path $ReleaseDir "meta.json"
    if (Test-Path $metaFile) {
        try {
            $meta = Get-Content $metaFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $builtAt = [string]$meta.builtAt
            if ($builtAt) {
                return [datetime]::Parse($builtAt)
            }
        } catch { }
    }
    return (Get-Item $ReleaseDir).LastWriteTimeUtc
}
