param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("MAIN", "DEV", "HOM")]
    [string]$Environment,
    [Parameter(Mandatory = $true)]
    [string]$TargetSha,
    [Parameter(Mandatory = $true)]
    [string]$TargetShaFull,
    [string]$RunId = "",
    [string]$Trigger = "manual"
)

<#
.SYNOPSIS
  Acquires Global\PPLID-Deploy-{ENV} then runs deploy_pipeline.ps1.
  Exit 2 = lock busy (another deploy holds the mutex).
#>

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\deploy_lock.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_paths.ps1")
. (Join-Path (Split-Path $PSScriptRoot -Parent) "lib\ops_store.ps1")
$opsRoot = Split-Path $PSScriptRoot -Parent
$baseDir = Split-Path $opsRoot -Parent
$orphanCleanup = Join-Path $opsRoot "lib\orphan_bot_cleanup.ps1"

if (-not (Enter-DeployLock -Environment $Environment)) {
    Write-OpsEnvLog -Environment $Environment -Service "lock" -Message "run_pipeline_locked: mutex busy, skip ($Trigger -> $TargetSha)."
    exit 2
}

try {
    if (Test-Path $orphanCleanup) {
        $cleanupLog = Join-Path (Get-PplidLogDir) "orphan-bots.log"
        try {
            $cleanupRaw = & $orphanCleanup -Mode cleanup -BaseDir $baseDir -LogPath $cleanupLog 2>&1 | Select-Object -Last 1
            $cleanupSummary = [string]$cleanupRaw
            try {
                $cleanupObj = $cleanupRaw | ConvertFrom-Json
                $cleanupSummary = "detected=$($cleanupObj.detected) stopped=$($cleanupObj.stopped) failed=$($cleanupObj.failed)"
            } catch { }
            Write-OpsEnvLog -Environment $Environment -Service "orphan-bots" -Message "cleanup: $cleanupSummary"
            if ($RunId) {
                $runDir = Join-Path (Join-Path $baseDir "deploy\$Environment\logs\runs") $RunId
                if (-not (Test-Path $runDir)) { New-Item -ItemType Directory -Path $runDir -Force | Out-Null }
                Add-Content -LiteralPath (Join-Path $runDir "pipeline.log") -Value "[$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))] [INFO] orphan-bots cleanup: $cleanupSummary" -Encoding UTF8
            }
        } catch {
            Write-OpsEnvLog -Environment $Environment -Service "orphan-bots" -Message "cleanup falhou (nao bloqueia pipeline): $($_.Exception.Message)"
        }
    }
    $pipeline = Join-Path $PSScriptRoot "deploy_pipeline.ps1"
    $args = @{
        Environment   = $Environment
        TargetSha     = $TargetSha
        TargetShaFull = $TargetShaFull
        Trigger       = $Trigger
    }
    if ($RunId) { $args.RunId = $RunId }
    & $pipeline @args
    exit $LASTEXITCODE
} finally {
    Exit-DeployLock -Environment $Environment
}
