param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("MAIN", "DEV", "HOM")]
    [string]$Environment,
    [Parameter(Mandatory = $true)]
    [string]$TargetSha,
    [Parameter(Mandatory = $true)]
    [string]$RunId
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\deploy_paths.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_state.ps1")
. (Join-Path $PSScriptRoot "lib\junction.ps1")
. (Join-Path $PSScriptRoot "lib\env_spec.ps1")
. (Join-Path $PSScriptRoot "lib\run_log.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_logging.ps1")
. (Join-Path $PSScriptRoot "lib\python_invoke.ps1")
. (Join-Path $PSScriptRoot "lib\legacy_deploy_status.ps1")
. (Join-Path $PSScriptRoot "lib\backend_deploy.ps1")

$spec = Get-PplidEnvSpec -Environment $Environment
$paths = Get-PplidDeployEnvPaths -Environment $Environment
$releaseDir = Get-PplidReleaseDir -Environment $Environment -Sha $TargetSha
$deployScript = Join-Path $spec.RepoDir "scripts\deploy"
$logName = "promote.log"

function LogInfo([string]$msg) { Write-DeployLogInfo -Environment $Environment -RunId $RunId -Message $msg -LogName $logName }
function LogOk([string]$msg) { Write-DeployLogOk -Environment $Environment -RunId $RunId -Message $msg -LogName $logName }
function LogWarn([string]$msg) { Write-DeployLogWarn -Environment $Environment -RunId $RunId -Message $msg -LogName $logName }
function LogErr([string]$msg) { Write-DeployLogError -Environment $Environment -RunId $RunId -Message $msg -LogName $logName }
function Log([string]$msg) { LogInfo $msg }

function Invoke-PplidDeployScript {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath,
        [hashtable]$Arguments = @{}
    )

    LogInfo "Executando: $ScriptPath"
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $output = @()
    try {
        $output = & $ScriptPath @Arguments 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }

    foreach ($line in @($output)) {
        $text = ($line | Out-String).Trim()
        if ($text) { LogInfo $text }
    }

    if ($code -ne 0) {
        $detail = if ($output) { ($output | Out-String).Trim() } else { "" }
        throw "Script falhou (exit $code): $ScriptPath${detail}"
    }
    LogOk "Concluido: $ScriptPath"
}

function Invoke-EnsureDatabaseSafe {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DeployScript,
        [Parameter(Mandatory = $true)]
        [string]$AppRoot
    )

    $backendDir = Join-Path $AppRoot "backend"
    $venvPython = Join-Path $backendDir ".venv\Scripts\python.exe"
    $ensureScript = Join-Path $DeployScript "ensure_database.py"
    if (-not (Test-Path $venvPython)) {
        throw "venv nao encontrado para ensure_database."
    }

    LogInfo "Executando ensure_database..."
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        $out = & $venvPython $ensureScript $backendDir 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }

    foreach ($line in @($out)) {
        $text = ($line | Out-String).Trim()
        if ($text) { LogInfo $text }
    }

    if ($code -ne 0) {
        $detail = if ($out) { ($out | Out-String).Trim() } else { "" }
        if ($detail) {
            throw "ensure_database falhou: $detail"
        }
        throw "ensure_database falhou."
    }

    LogOk ("Banco: " + (($out | Out-String).Trim()))
}

function Invoke-PplidPostPromoteHealthCheck {
    LogInfo "Aguardando servicos apos restart..."
    $healthScript = Join-Path $deployScript "health_check.ps1"
    Invoke-PplidDeployScript -ScriptPath $healthScript -Arguments @{
        Environment               = $Environment
        IncludeDashboardSmokeTest = $true
    }
    Test-PplidBackendRoutes -BackendPort $spec.BackendPort -AppRoot $paths.Current -Log {
        param($m)
        LogInfo $m
    }
}

if (-not (Test-Path $releaseDir)) {
    throw "Release nao encontrada: $releaseDir"
}

$state = Get-DeployState -Environment $Environment
$oldActive = [string]$state.activeSha
if (-not $oldActive) {
    $oldActive = [string](Get-PplidPreservedActiveSha -Environment $Environment -State $state)
}

Start-DeployStep -Environment $Environment -RunId $RunId -StepId "restart_services"
try {
    if ($oldActive) {
        $previousRelease = Get-PplidReleaseDir -Environment $Environment -Sha $oldActive.Trim()
        if (Test-Path $previousRelease) {
            Set-DirectoryJunction -LinkPath $paths.Previous -TargetPath $previousRelease
            LogInfo "previous -> $previousRelease"
        }
    }

    Set-DirectoryJunction -LinkPath $paths.Current -TargetPath $releaseDir
    LogInfo "current -> $releaseDir"

    $env:PPLID_APP_ROOT = $paths.Current
    $repoBackendEnv = Join-Path $spec.RepoDir "backend\.env"
    $releaseBackendEnv = Join-Path $releaseDir "backend\.env"
    if (Test-Path $repoBackendEnv) {
        Copy-Item $repoBackendEnv $releaseBackendEnv -Force
        LogInfo "backend/.env copiado do repo."
    }
    Invoke-PplidDeployScript -ScriptPath (Join-Path $deployScript "sync_env_files.ps1") -Arguments @{ Environment = $Environment }
    Invoke-EnsureDatabaseSafe -DeployScript $deployScript -AppRoot $paths.Current

    $backendDir = Join-Path $paths.Current "backend"
    $venvPython = Join-Path $backendDir ".venv\Scripts\python.exe"
    Push-Location $backendDir
    try {
        Invoke-PplidBackendMigrate -BackendDir $backendDir -VenvPython $venvPython -Log {
            param($m)
            LogInfo $m
        }
        LogInfo "collectstatic..."
        Invoke-PplidPython -Python $venvPython -Args @("manage.py", "collectstatic", "--noinput", "--skip-checks") -FailMessage "collectstatic falhou."
    } finally {
        Pop-Location
    }

    Invoke-PplidDeployScript -ScriptPath (Join-Path $deployScript "stop_env.ps1") -Arguments @{ Environment = $Environment }
    Start-Sleep -Seconds 3
    try {
        Invoke-PplidDeployScript -ScriptPath (Join-Path $deployScript "start_env.ps1") -Arguments @{ Environment = $Environment }
    } catch {
        LogErr "start_env falhou: $($_.Exception.Message)"
        LogInfo "Rollback apos falha no start..."
        Complete-DeployStep -Environment $Environment -RunId $RunId -StepId "restart_services" -Status "error" -ErrorMessage $_.Exception.Message
        & (Join-Path $PSScriptRoot "rollback_release.ps1") -Environment $Environment -RunId $RunId -Reason "start_failed"
        throw $_.Exception.Message
    }

    $deployLib = Join-Path $deployScript "lib.ps1"
    if (Test-Path $deployLib) {
        . $deployLib
        Set-DeployedShaFile -Environment $Environment -ShaShort $TargetSha
    }

    LogOk "Servicos reiniciados"
    Complete-DeployStep -Environment $Environment -RunId $RunId -StepId "restart_services" -Status "success"
} catch {
    if ((Get-DeploySteps -Environment $Environment -RunId $RunId | Where-Object { $_.id -eq "restart_services" }).status -ne "error") {
        Complete-DeployStep -Environment $Environment -RunId $RunId -StepId "restart_services" -Status "error" -ErrorMessage $_.Exception.Message
    }
    throw
}

Start-DeployStep -Environment $Environment -RunId $RunId -StepId "health_check"
try {
    Invoke-PplidPostPromoteHealthCheck
    LogOk "Health check OK"
    Complete-DeployStep -Environment $Environment -RunId $RunId -StepId "health_check" -Status "success"
} catch {
    $firstError = $_.Exception.Message
    LogErr "Health falhou pos-promote: $firstError"
    LogInfo "Tentando start_env novamente antes do rollback..."
    $recovered = $false
    try {
        Invoke-PplidDeployScript -ScriptPath (Join-Path $deployScript "start_env.ps1") -Arguments @{ Environment = $Environment }
        Start-Sleep -Seconds 5
        Invoke-PplidPostPromoteHealthCheck
        LogOk "Health check OK apos retry start_env"
        $recovered = $true
    } catch {
        LogWarn "Health ainda falhou apos retry start_env: $($_.Exception.Message)"
    }

    if ($recovered) {
        Complete-DeployStep -Environment $Environment -RunId $RunId -StepId "health_check" -Status "success"
    } else {
        LogInfo "Iniciando rollback..."
        Complete-DeployStep -Environment $Environment -RunId $RunId -StepId "health_check" -Status "error" -ErrorMessage $firstError
        & (Join-Path $PSScriptRoot "rollback_release.ps1") -Environment $Environment -RunId $RunId -Reason "health_failed"
        throw "Health check falhou apos promote."
    }
}

Start-DeployStep -Environment $Environment -RunId $RunId -StepId "publish_done"
LogOk "Promote OK."
Complete-DeployStep -Environment $Environment -RunId $RunId -StepId "publish_done" -Status "success"
