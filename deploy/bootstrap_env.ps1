param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("MAIN", "DEV", "HOM")]
    [string]$Environment
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\deploy_paths.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_state.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_lock.ps1")
. (Join-Path $PSScriptRoot "lib\env_spec.ps1")
. (Join-Path $PSScriptRoot "lib\shared_env.ps1")
. (Join-Path (Split-Path $PSScriptRoot -Parent) "lib\version_drift.ps1")
. (Join-Path (Split-Path $PSScriptRoot -Parent) "lib\port_utils.ps1")

$spec = Get-PplidEnvSpec -Environment $Environment
$paths = Get-PplidDeployEnvPaths -Environment $Environment
$deployScript = Join-Path $spec.RepoDir "scripts\deploy"
$logFile = Join-Path (Get-PplidLogDir) "PPLID_$Environment.log"

function Log([string]$msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logFile -Value "[$ts] [bootstrap] $msg" -Encoding UTF8
}

Initialize-PplidDeployLayout -Environment $Environment
$state = Sync-DeployStateFromLegacy -Environment $Environment

if ($state.status -in @("building", "validating", "promoting")) {
    Log "Pipeline busy ($($state.status)), skip bootstrap."
    exit 0
}

if (-not (Enter-DeployLock -Environment $Environment)) {
    Log "Deploy lock busy, skip bootstrap."
    exit 0
}

try {
    $activeSha = [string]$state.activeSha
    if (-not (Test-Path $paths.Current)) {
        if ($activeSha) {
            $releaseDir = Get-PplidReleaseDir -Environment $Environment -Sha $activeSha
            if (Test-Path $releaseDir) {
                . (Join-Path $PSScriptRoot "lib\junction.ps1")
                Set-DirectoryJunction -LinkPath $paths.Current -TargetPath $releaseDir
                Log "current junction criado -> $activeSha"
            } else {
                Log "Sem release $activeSha; usando repo ate primeiro pipeline."
                $env:PPLID_APP_ROOT = $spec.RepoDir
            }
        } else {
            Log "Sem activeSha; usando repo ate primeiro pipeline."
            $env:PPLID_APP_ROOT = $spec.RepoDir
        }
    } else {
        $env:PPLID_APP_ROOT = $paths.Current
    }

    $appRoot = if ($env:PPLID_APP_ROOT) { $env:PPLID_APP_ROOT } elseif (Test-Path $paths.Current) { $paths.Current } else { $spec.RepoDir }
    Log "Instalando env/media persistentes (shared) em $appRoot..."
    Install-PplidSharedRuntime -Environment $Environment -AppRoot $appRoot -RepoDir $spec.RepoDir
    $backendEnv = Join-Path $appRoot "backend\.env"
    if (-not (Test-Path $backendEnv)) {
        Log "backend/.env ainda ausente apos install shared; abortando start de $Environment."
        exit 1
    }

    $backendPort = $spec.BackendPort
    $portListening = Test-PortListening -Port $backendPort
    if ($portListening) {
        Log "Backend ja escutando :$backendPort"
        exit 0
    }

    $pgCheck = Test-PostgresAvailable -BackendDir (Join-Path $appRoot "backend")
    if (-not $pgCheck.Open) {
        $pgTarget = "$($pgCheck.HostName):$($pgCheck.Port)"
        Log "PostgreSQL indisponivel em $pgTarget. Inicie o servico PostgreSQL e rode deploy_all novamente."
        Write-Host "ERRO: PostgreSQL indisponivel em $pgTarget (ambiente $Environment)." -ForegroundColor Red
        Write-Host "Inicie o servico PostgreSQL local e execute deploy_all novamente." -ForegroundColor Yellow
        exit 1
    }

    Log "Subindo servicos de $Environment..."
    & (Join-Path $deployScript "start_env.ps1") -Environment $Environment
    Log "Bootstrap start concluido."
} finally {
    Exit-DeployLock -Environment $Environment
}
