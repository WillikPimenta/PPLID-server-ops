param(
    [ValidateSet("MAIN", "DEV", "HOM", "ALL")]
    [string]$TargetEnvironment = "ALL"
)

$ErrorActionPreference = "Stop"
$opsRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $opsRoot "lib\paths.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_paths.ps1")
. (Join-Path $PSScriptRoot "lib\env_spec.ps1")

function Get-PipelineDeployLibPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$EnvName
    )

    $envSpec = Get-PplidEnvSpec -Environment $EnvName
    $workspaceLib = Join-Path $envSpec.RepoDir "scripts\deploy\lib.ps1"
    if (Test-Path $workspaceLib) {
        return $workspaceLib
    }

    $mirrorLib = Join-Path (Get-PplidDeployEnvPaths -Environment $EnvName).Mirror "scripts\deploy\lib.ps1"
    if (Test-Path $mirrorLib) {
        return $mirrorLib
    }

    return $null
}

function Test-DeployLibScope {
    param(
        [Parameter(Mandatory = $true)]
        [string]$EnvName
    )

    $libPath = Get-PipelineDeployLibPath -EnvName $EnvName
    if (-not $libPath) {
        throw "lib.ps1 nao encontrado para $EnvName"
    }

    . $libPath

    foreach ($cmd in @("Get-CommitStatusUpdates", "Update-DeployStatus", "Get-DeployedShaFilePath")) {
        if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
            throw "$cmd ausente apos dot-source de $libPath"
        }
    }

    Write-Host "[OK] $EnvName -> $libPath"
}

$targets = if ($TargetEnvironment -eq "ALL") { @("MAIN", "DEV", "HOM") } else { @($TargetEnvironment) }
foreach ($name in $targets) {
    Test-DeployLibScope -EnvName $name
}

Write-Host "Smoke test pipeline lib scope: $($targets -join ', ') OK"
