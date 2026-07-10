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
. (Join-Path $PSScriptRoot "lib\run_log.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_logging.ps1")
. (Join-Path $PSScriptRoot "lib\python_invoke.ps1")
. (Join-Path $PSScriptRoot "lib\backend_deploy.ps1")

$releaseDir = Get-PplidReleaseDir -Environment $Environment -Sha $TargetSha
$logName = "validate.log"

function LogInfo([string]$msg) { Write-DeployLogInfo -Environment $Environment -RunId $RunId -Message $msg -LogName $logName }
function LogOk([string]$msg) { Write-DeployLogOk -Environment $Environment -RunId $RunId -Message $msg -LogName $logName }
function LogWarn([string]$msg) { Write-DeployLogWarn -Environment $Environment -RunId $RunId -Message $msg -LogName $logName }
function Log([string]$msg) { LogInfo $msg }

$backendDir = Join-Path $releaseDir "backend"
$frontendDist = Join-Path $releaseDir "frontend\dist"
$metaFile = Join-Path $releaseDir "meta.json"
$venvPython = Join-Path $backendDir ".venv\Scripts\python.exe"

Start-DeployStep -Environment $Environment -RunId $RunId -StepId "validate"

if (-not (Test-Path $metaFile)) { throw "meta.json ausente em release." }
$meta = Get-Content $metaFile -Raw | ConvertFrom-Json
if ($meta.built -ne $true) { throw "Release nao marcada como built." }
if (-not (Test-Path $venvPython)) { throw "venv ausente." }
if (-not (Test-Path $frontendDist)) { throw "frontend/dist ausente." }

$backendChanged = $false
if ($null -ne $meta.backendChanged) {
    $backendChanged = [bool]$meta.backendChanged
}
if (-not $backendChanged -and $meta.backendPaths -and @($meta.backendPaths).Count -gt 0) {
    $backendChanged = $true
}

$warnings = [System.Collections.ArrayList]@()

try {
    if ($backendChanged) {
        LogInfo "Validacao backend (release com mudancas em backend/)."
        Invoke-PplidPython -Python $venvPython -Args @(
            "manage.py", "check", "--deploy"
        ) -WorkingDirectory $backendDir -FailMessage "manage.py check --deploy falhou."
        Invoke-PplidBackendMigratePlan -BackendDir $backendDir -VenvPython $venvPython -Log {
            param($m)
            LogInfo $m
        }
    } else {
        try {
            Invoke-PplidPython -Python $venvPython -Args @(
                "manage.py", "check", "--deploy"
            ) -WorkingDirectory $backendDir -FailMessage "manage.py check --deploy falhou."
        } catch {
            $msg = $_.Exception.Message
            LogWarn "manage.py check --deploy falhou (aviso, sem mudanca backend): $msg"
            [void]$warnings.Add(@{
                name   = "manage.py check --deploy"
                level  = "warning"
                passed = $false
            })
        }
    }

    if ($warnings.Count -gt 0) {
        $warnFile = Join-Path (Get-PplidDeployEnvPaths -Environment $Environment).Logs "runs\$RunId\validate-warnings.json"
        $warnDir = Split-Path $warnFile -Parent
        if (-not (Test-Path $warnDir)) {
            New-Item -ItemType Directory -Path $warnDir -Force | Out-Null
        }
        $utf8 = New-Object System.Text.UTF8Encoding $false
        [System.IO.File]::WriteAllText($warnFile, ($warnings | ConvertTo-Json -Depth 5), $utf8)
        Complete-DeployStep -Environment $Environment -RunId $RunId -StepId "validate" -Status "warning"
    } else {
        LogOk "Validacao OK para $TargetSha."
        Complete-DeployStep -Environment $Environment -RunId $RunId -StepId "validate" -Status "success"
    }
} catch {
    Complete-DeployStep -Environment $Environment -RunId $RunId -StepId "validate" -Status "error" -ErrorMessage $_.Exception.Message
    throw
}
