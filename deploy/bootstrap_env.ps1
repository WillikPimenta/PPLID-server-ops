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
. (Join-Path (Split-Path $PSScriptRoot -Parent) "lib\ops_store.ps1")

function Log([string]$msg) {
    Write-OpsEnvLog -Environment $Environment -Service "bootstrap" -Message "[bootstrap] $msg"
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
    . (Join-Path $PSScriptRoot "lib\junction.ps1")
    $activeSha = [string]$state.activeSha
    $releaseDir = $null
    if ($activeSha) {
        $candidateRelease = Get-PplidReleaseDir -Environment $Environment -Sha $activeSha
        if (Test-Path $candidateRelease) {
            $releaseDir = $candidateRelease
        }
    }

    $currentOk = (Test-Path $paths.Current) -and (Test-PplidPathIsReparsePoint -Path $paths.Current)
    if (-not $currentOk) {
        if ($releaseDir) {
            if (Test-Path $paths.Current) {
                Log "current existe e nao e junction; reparando residual antes do bootstrap."
            }
            Set-DirectoryJunction -LinkPath $paths.Current -TargetPath $releaseDir -AllowReplaceResidualDirectory
            Log "current junction criado -> $activeSha"
            $currentOk = $true
        } elseif (Test-Path $paths.Current) {
            Log "current residual sem release ativa; usando repo ate primeiro pipeline."
            $env:PPLID_APP_ROOT = $spec.RepoDir
        } else {
            Log "Sem activeSha/release; usando repo ate primeiro pipeline."
            $env:PPLID_APP_ROOT = $spec.RepoDir
        }
    }

    if ($currentOk) {
        $env:PPLID_APP_ROOT = $paths.Current
    }

    # Sempre instalar shared no release fisico (nunca materializar current como pasta real).
    $appRoot = if ($releaseDir) { $releaseDir } elseif ($currentOk) { $paths.Current } elseif ($env:PPLID_APP_ROOT) { $env:PPLID_APP_ROOT } else { $spec.RepoDir }
    Log "Instalando env/media persistentes (shared) em $appRoot..."
    Install-PplidSharedRuntime -Environment $Environment -AppRoot $appRoot -RepoDir $spec.RepoDir
    $backendEnv = Join-Path $appRoot "backend\.env"
    if (-not (Test-Path $backendEnv)) {
        Log "backend/.env ainda ausente apos install shared; abortando start de $Environment."
        exit 1
    }

    $backendPort = $spec.BackendPort
    $pgCheck = Test-PostgresAvailable -BackendDir (Join-Path $appRoot "backend")
    if (-not $pgCheck.Open) {
        $pgTarget = "$($pgCheck.HostName):$($pgCheck.Port)"
        Log "PostgreSQL indisponivel em $pgTarget. Inicie o servico PostgreSQL e rode deploy_all novamente."
        Write-Host "ERRO: PostgreSQL indisponivel em $pgTarget (ambiente $Environment)." -ForegroundColor Red
        Write-Host "Inicie o servico PostgreSQL local e execute deploy_all novamente." -ForegroundColor Yellow
        exit 1
    }

    # Sempre aplica migrations pendentes no bootstrap (mesmo se o backend ja estiver no ar).
    # Evita health "degraded" por schema atrasado apos reboot/start sem promote.
    $backendDir = Join-Path $appRoot "backend"
    $venvPython = Join-Path $backendDir ".venv\Scripts\python.exe"
    if ((Test-Path $venvPython) -and (Test-Path (Join-Path $backendDir "manage.py"))) {
        . (Join-Path $PSScriptRoot "lib\backend_deploy.ps1")
        Log "Aplicando migrations pendentes (bootstrap)..."
        Invoke-PplidBackendMigrate -BackendDir $backendDir -VenvPython $venvPython -Log {
            param($m)
            Log $m
        }
    } else {
        Log "venv/manage.py ausente; migrate adiado para o start_env/promote."
    }

    $portListening = Test-PortListening -Port $backendPort
    if ($portListening) {
        Log "Backend ja escutando :$backendPort (migrations conferidas)."
        exit 0
    }

    Log "Subindo servicos de $Environment..."
    & (Join-Path $deployScript "start_env.ps1") -Environment $Environment
    Log "Bootstrap start concluido."
} finally {
    Exit-DeployLock -Environment $Environment
}
