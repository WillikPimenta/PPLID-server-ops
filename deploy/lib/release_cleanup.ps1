. (Join-Path $PSScriptRoot "deploy_paths.ps1")
. (Join-Path $PSScriptRoot "git_invoke.ps1")

function Enter-PplidReleaseMaintenanceLock {
    param([string]$Environment)
    $script:PplidReleaseMutex = New-Object System.Threading.Mutex($false, "Global\PPLID-Deploy-$Environment")
    $owned = $false
    try { $owned = $script:PplidReleaseMutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $owned = $true }
    if (-not $owned) { $script:PplidReleaseMutex.Dispose(); throw 'Deploy em andamento; manutencao de releases bloqueada.' }
}

function Exit-PplidReleaseMaintenanceLock {
    $script:PplidReleaseMutex.ReleaseMutex()
    $script:PplidReleaseMutex.Dispose()
}

function Get-PplidProtectedReleaseShas {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment
    )

    . (Join-Path $PSScriptRoot "deploy_state.ps1")
    $paths = Get-PplidDeployEnvPaths -Environment $Environment
    $protected = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $backgroundPath = Join-Path $paths.Root 'bluegreen\background.json'
    if (Test-Path -LiteralPath $backgroundPath) {
        $background = Get-Content -LiteralPath $backgroundPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        if ($background.sha) { [void]$protected.Add([string]$background.sha) }
    }
    $blueGreenState = Join-Path $paths.Root 'bluegreen\state.json'
    if (Test-Path -LiteralPath $blueGreenState) {
        # Invalid state must block pruning, not silently unprotect live code.
        $runtime = Get-Content -LiteralPath $blueGreenState -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        if ($runtime.schemaVersion -ne 1) { throw 'Estado blue-green desconhecido; prune bloqueado.' }
        foreach ($property in $runtime.slots.PSObject.Properties) {
            if ($property.Value.sha) { [void]$protected.Add([string]$property.Value.sha) }
        }
    }

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
        [string]$ReleaseDir,
        [switch]$AllowBuildTarget
    )

    if (-not (Test-Path $ReleaseDir)) { return }

    $paths = Get-PplidDeployEnvPaths -Environment $Environment
    $resolvedParent = [IO.Path]::GetFullPath((Split-Path -Parent $ReleaseDir)).TrimEnd('\')
    $expectedParent = [IO.Path]::GetFullPath($paths.Releases).TrimEnd('\')
    if ($resolvedParent -ne $expectedParent) { throw 'Release fora do diretorio permitido.' }
    $protected = Get-PplidProtectedReleaseShas -Environment $Environment
    $leaf = Split-Path -Leaf $ReleaseDir
    if (@($protected) -contains $leaf) {
        $state = Get-DeployState -Environment $Environment
        if (-not $AllowBuildTarget -or $state.targetSha -ne $leaf -or
            $leaf -in @($state.activeSha, $state.previousSha, $state.lastGoodSha)) {
            throw 'Release protegida; remocao recusada.'
        }
        foreach ($link in @($paths.Current, $paths.Previous)) {
            if (Test-Path -LiteralPath $link) {
                $target = @((Get-Item -LiteralPath $link -Force).Target)[0]
                if ($target -and (Split-Path -Leaf $target) -eq $leaf) { throw 'Junction protege a release.' }
            }
        }
        $runtimePath = Join-Path $paths.Root 'bluegreen\state.json'
        if (Test-Path -LiteralPath $runtimePath) {
            $runtime = Get-Content -LiteralPath $runtimePath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
            foreach ($property in $runtime.slots.PSObject.Properties) {
                if ($property.Value.sha -eq $leaf) { throw 'Slot protege a release.' }
            }
        }
        $backgroundPath = Join-Path $paths.Root 'bluegreen\background.json'
        if (Test-Path -LiteralPath $backgroundPath) {
            $background = Get-Content -LiteralPath $backgroundPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
            if ($background.sha -eq $leaf) { throw 'Worker independente protege a release.' }
        }
    }
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
