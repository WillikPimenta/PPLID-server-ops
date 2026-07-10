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

$ErrorActionPreference = "Stop"
$opsRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $opsRoot "lib\paths.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_state.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_paths.ps1")
. (Join-Path $PSScriptRoot "lib\env_spec.ps1")
. (Join-Path $PSScriptRoot "lib\run_log.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_logging.ps1")
. (Join-Path $PSScriptRoot "lib\legacy_deploy_status.ps1")

if (-not $RunId) { $RunId = New-DeployRunId }
$spec = Get-PplidEnvSpec -Environment $Environment
$repoDeploy = Join-Path $spec.RepoDir "scripts\deploy"
$logFile = Join-Path (Get-PplidLogDir) "PPLID_$Environment.log"
$startedAt = (Get-Date).ToString("o")
$script:PipelineFailedStep = ""

function Get-PipelineCommitRepo {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Environment,
        [Parameter(Mandatory = $true)]
        [string]$FallbackRepo
    )
    $mirrorDir = (Get-PplidDeployEnvPaths -Environment $Environment).Mirror
    if (Test-Path (Join-Path $mirrorDir ".git")) {
        return $mirrorDir
    }
    return $FallbackRepo
}

function Resolve-PipelineTargetShaFull {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Environment,
        [Parameter(Mandatory = $true)]
        [string]$TargetSha,
        [string]$TargetShaFull = ""
    )
    if ($TargetShaFull.Length -ge 40) { return $TargetShaFull }
    $mirrorDir = (Get-PplidDeployEnvPaths -Environment $Environment).Mirror
    if (-not (Test-Path (Join-Path $mirrorDir ".git"))) { return $TargetShaFull }
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    Push-Location $mirrorDir
    try {
        git fetch origin 2>$null | Out-Null
        $full = git rev-parse $TargetSha 2>$null
        if ($full) { return $full.Trim() }
    } finally {
        Pop-Location
        $ErrorActionPreference = $prevEap
    }
    return $TargetShaFull
}

$TargetShaFull = Resolve-PipelineTargetShaFull -Environment $Environment -TargetSha $TargetSha -TargetShaFull $TargetShaFull
$commitRepoDir = Get-PipelineCommitRepo -Environment $Environment -FallbackRepo $spec.RepoDir
if ($env:PPLID_PROMOTE_SOURCE) {
    Write-DeployLogInfo -Environment $Environment -RunId $RunId -Message "Promote source: $($env:PPLID_PROMOTE_SOURCE)" -LogName "pipeline.log"
}

function Write-PipelineLog([string]$msg, [string]$Level = "INFO") {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logFile -Value "[$ts] $msg" -Encoding UTF8
    Write-DeployLogEntry -Environment $Environment -RunId $RunId -Level $Level -Message $msg -LogName "pipeline.log"
}

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

    $mirrorDir = (Get-PplidDeployEnvPaths -Environment $EnvName).Mirror
    $mirrorLib = Join-Path $mirrorDir "scripts\deploy\lib.ps1"
    if (Test-Path $mirrorLib) {
        return $mirrorLib
    }

    return $null
}

function Assert-PipelineDeployStatusLib {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LibPath
    )

    if (-not (Get-Command Get-CommitStatusUpdates -ErrorAction SilentlyContinue)) {
        throw "lib.ps1 carregado sem Get-CommitStatusUpdates: $LibPath"
    }
    if (-not (Get-Command Update-DeployStatus -ErrorAction SilentlyContinue)) {
        throw "lib.ps1 carregado sem Update-DeployStatus: $LibPath"
    }
}

$deployStatusLib = Get-PipelineDeployLibPath -EnvName $Environment
$hasDeployStatusLib = $false
. (Join-Path $PSScriptRoot "lib\deploy_status_bridge.ps1")
if ($deployStatusLib) {
    . $deployStatusLib
    Assert-PipelineDeployStatusLib -LibPath $deployStatusLib
    #region agent log
    Write-PipelineDebugLog -HypothesisId "B" -Location "deploy_pipeline.ps1:lib-load" `
        -Message "deploy status lib loaded" -RunId $RunId -Data @{
            libPath          = $deployStatusLib
            supportsExtended = (Test-PipelineDeployStatusSupportsExtendedEvents)
        }
    #endregion
    $hasDeployStatusLib = $true
}

Sync-DeployStateFromLegacy -Environment $Environment | Out-Null
$state = Get-DeployState -Environment $Environment
$oldActive = Get-PplidPreservedActiveSha -Environment $Environment -State $state

Initialize-DeployRun -Environment $Environment -RunId $RunId -Trigger $Trigger -TargetSha $TargetSha
Initialize-DeploySteps -Environment $Environment -RunId $RunId | Out-Null
Start-DeployStep -Environment $Environment -RunId $RunId -StepId "prepare"

$commitSubject = ""
$commitAuthor = ""

try {
    $buildingUpdates = @{
        status    = "building"
        targetSha = $TargetSha
        runId     = $RunId
        startedAt = $startedAt
        lastError = $null
    }
    if ($oldActive) {
        $buildingUpdates.activeSha = $oldActive
        if (-not $state.lastGoodSha) {
            $buildingUpdates.lastGoodSha = $oldActive
        }
    }
    Set-DeployState -Environment $Environment -Updates $buildingUpdates

    if ($hasDeployStatusLib) {
        $commitUpdates = Get-CommitStatusUpdates -RepoDir $commitRepoDir -Revision $TargetShaFull
        $commitSubject = [string]$commitUpdates.gitCommitSubject
        $commitAuthor = [string]$commitUpdates.gitCommitAuthor
        $startedUpdates = @{
            phase               = "deploying"
            lastDeployStartedAt = $startedAt
            lastDeployResult    = $null
            gitSha              = $TargetSha
            gitShaFull          = $TargetShaFull
        }
        foreach ($key in $commitUpdates.Keys) {
            $startedUpdates[$key] = $commitUpdates[$key]
        }
        Invoke-PipelineDeployStatusUpdate -Environment $Environment -Updates $startedUpdates `
            -EventType "deploy_started" -EventMessage "Pipeline $TargetSha ($Trigger)" `
            -EventSha $TargetSha -EventSubject $commitSubject -EventAuthor $commitAuthor `
            -EventRunId $RunId -EventPreviousSha $oldActive -EventStartedAt $startedAt -DebugRunId $RunId
    }

    Write-DeployLogInfo -Environment $Environment -RunId $RunId -Message "Ambiente: $Environment"
    Write-DeployLogInfo -Environment $Environment -RunId $RunId -Message "Branch: $($spec.Branch)"
    Write-DeployLogInfo -Environment $Environment -RunId $RunId -Message "Commit atual: $oldActive"
    Write-DeployLogInfo -Environment $Environment -RunId $RunId -Message "Novo commit: $TargetSha"
    if ($commitAuthor) { Write-DeployLogInfo -Environment $Environment -RunId $RunId -Message "Autor: $commitAuthor" }
    if ($commitSubject) { Write-DeployLogInfo -Environment $Environment -RunId $RunId -Message "Mensagem: $commitSubject" }
    Write-DeployLogInfo -Environment $Environment -RunId $RunId -Message "Run: $RunId"
    Write-DeployLogInfo -Environment $Environment -RunId $RunId -Message "Inicio: $startedAt"
    Write-DeployLogInfo -Environment $Environment -RunId $RunId -Message "Trigger: $Trigger"

    Complete-DeployStep -Environment $Environment -RunId $RunId -StepId "prepare" -Status "success"

    Write-PipelineLog "Pipeline iniciado: $oldActive -> $TargetSha"
    & (Join-Path $PSScriptRoot "build_staging.ps1") -Environment $Environment -TargetSha $TargetSha -TargetShaFull $TargetShaFull -RunId $RunId

    Set-DeployState -Environment $Environment -Updates @{ status = "validating" }
    & (Join-Path $PSScriptRoot "validate_staging.ps1") -Environment $Environment -TargetSha $TargetSha -RunId $RunId

    Set-DeployState -Environment $Environment -Updates @{ status = "promoting" }
    & (Join-Path $PSScriptRoot "promote_release.ps1") -Environment $Environment -TargetSha $TargetSha -RunId $RunId

    $finishedAt = (Get-Date).ToString("o")
    $durationSec = 0
    try {
        $durationSec = [int]([datetime]::Parse($finishedAt) - [datetime]::Parse($startedAt)).TotalSeconds
    } catch { }

    Set-DeployState -Environment $Environment -Updates @{
        status        = "idle"
        activeSha     = $TargetSha
        previousSha   = $oldActive
        lastGoodSha   = $TargetSha
        finishedAt    = $finishedAt
        lastError     = $null
        blockedSha    = $null
        blockedAt     = $null
        blockedReason = $null
        blockedRunId  = $null
    }

    Update-DeployManifest -Environment $Environment -RunId $RunId -Updates @{
        finishedAt = $finishedAt
        result     = "success"
        fromSha    = $oldActive
        toSha      = $TargetSha
    }

    Write-DeployRunSummary -Environment $Environment -RunId $RunId -Summary @{
        environment   = $Environment
        runId         = $RunId
        branch        = $spec.Branch
        fromSha       = $oldActive
        toSha         = $TargetSha
        subject       = $commitSubject
        author        = $commitAuthor
        startedAt     = $startedAt
        finishedAt    = $finishedAt
        durationSec   = $durationSec
        result        = "success"
        failedStep    = $null
        trigger       = $Trigger
    }

    if ($hasDeployStatusLib) {
        Set-DeployedShaFile -Environment $Environment -ShaShort $TargetSha -ShaFull $TargetShaFull

        $warnFile = Join-Path (Get-PplidDeployEnvPaths -Environment $Environment).Logs "runs\$RunId\validate-warnings.json"
        $checks = @()
        $deployResult = "success"
        $deployMessage = ""
        if (Test-Path $warnFile) {
            try {
                $warnData = Get-Content $warnFile -Raw -Encoding UTF8 | ConvertFrom-Json
                foreach ($w in @($warnData)) {
                    $checks += @{
                        name   = $w.name
                        level  = $w.level
                        passed = [bool]$w.passed
                    }
                }
                if ($checks.Count -gt 0) {
                    $deployResult = "warning"
                    $deployMessage = "Validacao com avisos: $($checks[0].name)"
                }
            } catch { }
        }

        $commitUpdates = Get-CommitStatusUpdates -RepoDir $commitRepoDir -Revision $TargetShaFull
        $successUpdates = @{
            phase                     = "healthy"
            lastDeployFinishedAt      = $finishedAt
            lastDeployResult          = $deployResult
            lastDeployMessage         = if ($deployResult -eq "success") { "" } else { $deployMessage }
            lastDeploy                = @{
                result     = $deployResult
                message    = $deployMessage
                finishedAt = $finishedAt
                checks     = $checks
            }
            deployedSha               = $TargetSha
            deployedShaFull           = $TargetShaFull
            deployedAt                = $finishedAt
            gitSha                    = $TargetSha
            gitShaFull                = $TargetShaFull
            updatePending             = $false
        }
        foreach ($key in $commitUpdates.Keys) {
            $successUpdates[$key] = $commitUpdates[$key]
        }
        $successMsg = if ($deployResult -eq "warning") { "Pipeline concluido com avisos ($TargetSha)" } else { "Pipeline concluido ($TargetSha)" }
        Invoke-PipelineDeployStatusUpdate -Environment $Environment -Updates $successUpdates `
            -EventType "deploy_success" -EventMessage $successMsg `
            -EventSha $TargetSha -EventSubject $commitSubject -EventAuthor $commitAuthor `
            -EventRunId $RunId -EventPreviousSha $oldActive -EventStartedAt $startedAt `
            -EventFinishedAt $finishedAt -EventDurationSeconds $durationSec -EventResult $deployResult -DebugRunId $RunId
    }

    . (Join-Path $PSScriptRoot "lib\sync_workspace_repo.ps1")
    Sync-PplidWorkspaceRepo -Environment $Environment -TargetShaFull $TargetShaFull -LogFile $logFile

    try {
        & (Join-Path $PSScriptRoot "prune_releases.ps1") -Environment $Environment -KeepCount 5 2>&1 | ForEach-Object {
            Write-PipelineLog $_
        }
    } catch {
        Write-PipelineLog "Prune releases aviso: $($_.Exception.Message)" "WARN"
    }

    Write-DeployLogSuccess -Environment $Environment -RunId $RunId -Message "Deploy finalizado com sucesso"
    Write-PipelineLog "Pipeline concluido com sucesso." "SUCCESS"
    exit 0
} catch {
    $errRaw = $_.Exception.Message
    $err = ($errRaw -replace '\x1b\[[0-9;]*m', '' -replace "[\r\n]+", " " -replace '\s+', ' ').Trim()
    if ($err.Length -gt 500) { $err = $err.Substring(0, 500) }
    $finishedAt = (Get-Date).ToString("o")
    $failedStep = Get-FailedDeployStepId -Environment $Environment -RunId $RunId
    if (-not $failedStep) { $failedStep = $script:PipelineFailedStep }

    Write-DeployLogError -Environment $Environment -RunId $RunId -Message $err
    if ($failedStep) {
        Skip-RemainingDeploySteps -Environment $Environment -RunId $RunId -AfterStepId $failedStep
    }

    . (Join-Path $PSScriptRoot "lib\legacy_deploy_status.ps1")
    $preserveActive = Get-PplidPreservedActiveSha -Environment $Environment -State $state
    if (-not $preserveActive -and $oldActive) {
        $preserveActive = $oldActive
    }
    $preserveGood = if ($state.lastGoodSha) { $state.lastGoodSha } else { $preserveActive }
    Set-DeployState -Environment $Environment -Updates @{
        status        = "failed"
        lastError     = $err
        finishedAt    = $finishedAt
        activeSha     = $preserveActive
        lastGoodSha   = $preserveGood
        targetSha     = $TargetSha
        blockedSha    = $TargetSha
        blockedAt     = $finishedAt
        blockedReason = $failedStep
        blockedRunId  = $RunId
    }

    $durationSec = 0
    try {
        $durationSec = [int]([datetime]::Parse($finishedAt) - [datetime]::Parse($startedAt)).TotalSeconds
    } catch { }

    Update-DeployManifest -Environment $Environment -RunId $RunId -Updates @{
        finishedAt = $finishedAt
        result     = "failed"
        failedStep = $failedStep
    }

    Write-DeployRunSummary -Environment $Environment -RunId $RunId -Summary @{
        environment = $Environment
        runId       = $RunId
        branch      = $spec.Branch
        fromSha     = $oldActive
        toSha       = $TargetSha
        subject     = $commitSubject
        author      = $commitAuthor
        startedAt   = $startedAt
        finishedAt  = $finishedAt
        durationSec = $durationSec
        result      = "failed"
        failedStep  = $failedStep
        trigger     = $Trigger
        lastError   = $err
    }

    $summaryPath = Get-DeployRunSummaryPath -Environment $Environment -RunId $RunId
    if (Test-Path $summaryPath) {
        try {
            $existing = Get-Content $summaryPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($existing.errorDetail) {
                $merged = @{
                    environment = $Environment
                    runId       = $RunId
                    branch      = $spec.Branch
                    fromSha     = $oldActive
                    toSha       = $TargetSha
                    subject     = $commitSubject
                    author      = $commitAuthor
                    startedAt   = $startedAt
                    finishedAt  = $finishedAt
                    durationSec = $durationSec
                    result      = "failed"
                    failedStep  = $failedStep
                    trigger     = $Trigger
                    lastError   = $err
                    errorDetail = $existing.errorDetail
                }
                Write-DeployRunSummary -Environment $Environment -RunId $RunId -Summary $merged
            }
        } catch { }
    }

    if ($hasDeployStatusLib) {
        $originShaShort = ""
        try {
            $mirrorDir = (Get-PplidDeployEnvPaths -Environment $Environment).Mirror
            if (Test-Path $mirrorDir) {
                Push-Location $mirrorDir
                try {
                    $originLine = (git rev-parse --short "origin/$($spec.Branch)" 2>$null)
                    if ($originLine) { $originShaShort = $originLine.Trim() }
                } finally { Pop-Location }
            }
        } catch { }
        $targetShort = if ($TargetSha.Length -ge 7) { $TargetSha.Substring(0, 7) } else { $TargetSha }
        $updatePending = $false
        if ($originShaShort -and $targetShort) {
            $updatePending = ($originShaShort.Substring(0, [Math]::Min(7, $originShaShort.Length)) -ne $targetShort.Substring(0, [Math]::Min(7, $targetShort.Length)))
        }
        Invoke-PipelineDeployStatusUpdate -Environment $Environment -Updates @{
            phase                = "failed"
            lastDeployResult     = "failed"
            lastDeployMessage    = $err
            lastDeployFinishedAt = $finishedAt
            updatePending        = $updatePending
        } -EventType "deploy_failed" -EventMessage $err `
            -EventSha $TargetSha -EventSubject $commitSubject -EventAuthor $commitAuthor `
            -EventRunId $RunId -EventPreviousSha $oldActive -EventStartedAt $startedAt `
            -EventFinishedAt $finishedAt -EventDurationSeconds $durationSec -EventResult "failed" `
            -EventFailedStep $failedStep -DebugRunId $RunId
    }

    Write-PipelineLog "Pipeline FALHOU: $err" "ERROR"
    exit 1
}
