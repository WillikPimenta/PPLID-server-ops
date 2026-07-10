param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("MAIN", "DEV", "HOM")]
    [string]$Environment,
    [int]$KeepCount = 5,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\deploy_paths.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_state.ps1")
. (Join-Path $PSScriptRoot "lib\release_cleanup.ps1")
. (Join-Path $PSScriptRoot "lib\legacy_deploy_status.ps1")

$paths = Get-PplidDeployEnvPaths -Environment $Environment
if (-not (Test-Path $paths.Releases)) {
    Write-Host "Nenhuma release em $($paths.Releases)"
    exit 0
}

$protected = Get-PplidProtectedReleaseShas -Environment $Environment
$releases = Get-ChildItem $paths.Releases -Directory | ForEach-Object {
    [PSCustomObject]@{
        Sha  = $_.Name
        Path = $_.FullName
        Sort = Get-PplidReleaseSortDate -ReleaseDir $_.FullName
    }
}

$toKeep = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($sha in $protected) {
    if ($sha) { [void]$toKeep.Add($sha) }
}

$sorted = $releases | Sort-Object Sort -Descending
$slot = 0
foreach ($rel in $sorted) {
    if ($toKeep.Contains($rel.Sha)) { continue }
    if ($slot -lt $KeepCount) {
        [void]$toKeep.Add($rel.Sha)
        $slot++
    }
}

$removed = @()
foreach ($rel in $sorted) {
    if ($toKeep.Contains($rel.Sha)) { continue }
    if ($DryRun) {
        Write-Host "[dry-run] Removeria: $($rel.Sha)"
        $removed += $rel.Sha
        continue
    }
    Write-Host "Removendo release: $($rel.Sha)"
    Remove-PplidReleaseDir -Environment $Environment -ReleaseDir $rel.Path
    $removed += $rel.Sha
}

Sync-DeployStateFromLegacy -Environment $Environment | Out-Null
Write-Host "Prune $Environment concluido. Mantidas: $($toKeep.Count), removidas: $($removed.Count), protegidas: $($protected.Count)"
if ($removed.Count) {
    Write-Host ("Removidas: " + ($removed -join ", "))
}
