param(
    [switch]$Quick
)

$ErrorActionPreference = "Stop"
$opsRoot = $PSScriptRoot
$failed = 0
$results = @()

function Add-Result {
    param([string]$Check, [bool]$Ok, [string]$Detail = "")
    $script:results += [PSCustomObject]@{ Check = $Check; Status = if ($Ok) { "OK" } else { "FAIL" }; Detail = $Detail }
    if (-not $Ok) { $script:failed++ }
}

. (Join-Path $opsRoot "lib\paths.ps1")
. (Join-Path $opsRoot "lib\version_drift.ps1")
. (Join-Path $opsRoot "deploy\lib\deploy_paths.ps1")
. (Join-Path $opsRoot "deploy\lib\deploy_state.ps1")
. (Join-Path $opsRoot "deploy\lib\deploy_lock.ps1")

foreach ($env in @("MAIN", "DEV", "HOM")) {
    $paths = Get-PplidDeployEnvPaths -Environment $env
    Add-Result "layout_$env" (Test-Path $paths.StateFile) $paths.StateFile
    $state = Get-DeployState -Environment $env
    $stale = Test-DeployStateStale -State $state -MaxDeployMinutes 45
    Add-Result "not_stale_$env" (-not $stale) "status=$($state.status)"

    if ($state.activeSha) {
        $hasCurrent = Test-Path $paths.Current
        Add-Result "current_junction_$env" $hasCurrent $paths.Current
        $deployedSha = Get-PplidDeployedSha -Environment $env
        $shaMatch = Test-PplidShaMatch -Left $state.activeSha -Right $deployedSha
        Add-Result "sha_coherent_$env" $shaMatch "active=$($state.activeSha) deployed=$deployedSha"
    }

    $legacy = Get-PplidDeployStatusEnvironment -Environment $env
    if ($legacy -and $legacy.phase -eq "deploying" -and $legacy.lastDeployStartedAt) {
        try {
            $age = ((Get-Date) - [datetime]::Parse($legacy.lastDeployStartedAt)).TotalMinutes
            Add-Result "not_stuck_deploying_$env" ($age -lt 45) "phase=deploying age=${age}m"
        } catch {
            Add-Result "not_stuck_deploying_$env" $false "phase=deploying invalid timestamp"
        }
    } else {
        Add-Result "not_stuck_deploying_$env" $true "phase=$($legacy.phase)"
    }
}

& (Join-Path $opsRoot "verify_stack.ps1")
Add-Result "verify_stack" ($LASTEXITCODE -eq 0) "exit=$LASTEXITCODE"

if (-not $Quick) {
    $lock1 = Enter-DeployLock -Environment "DEV"
    $lock2 = $false
    if ($lock1) {
        $lockTestScript = Join-Path $env:TEMP "pplid_lock_test.ps1"
        @"
`$ErrorActionPreference = 'Stop'
. '$opsRoot\deploy\lib\deploy_paths.ps1'
. '$opsRoot\deploy\lib\deploy_state.ps1'
. '$opsRoot\deploy\lib\deploy_lock.ps1'
if (Enter-DeployLock -Environment DEV) { exit 1 } else { exit 0 }
"@ | Set-Content -Path $lockTestScript -Encoding UTF8
        $lock2Proc = Start-Process powershell -ArgumentList @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $lockTestScript
        ) -Wait -PassThru -WindowStyle Hidden
        $lock2 = ($lock2Proc.ExitCode -eq 1)
        Remove-Item -LiteralPath $lockTestScript -Force -ErrorAction SilentlyContinue
        Exit-DeployLock -Environment "DEV"
    }
    Add-Result "lock_exclusive_DEV" ($lock1 -and -not $lock2) "second=$lock2"
}

$results | Format-Table -AutoSize
if ($failed -gt 0) { exit 1 }
Write-Host "Validacao deploy OK."
exit 0
