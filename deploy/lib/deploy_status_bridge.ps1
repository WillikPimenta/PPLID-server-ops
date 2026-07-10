function Test-PipelineDeployStatusSupportsExtendedEvents {
    if (-not (Get-Command Update-DeployStatus -ErrorAction SilentlyContinue)) {
        return $false
    }
    return (Get-Command Update-DeployStatus).Parameters.ContainsKey("EventSha")
}

function Get-PipelineDebugLogPath {
    if (Get-Command Get-PplidBaseDir -ErrorAction SilentlyContinue) {
        return Join-Path (Get-PplidBaseDir) "debug-079b97.log"
    }
    return Join-Path (Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent) "debug-079b97.log"
}

function Write-PipelineDebugLog {
    param(
        [string]$HypothesisId,
        [string]$Location,
        [string]$Message,
        [hashtable]$Data = @{},
        [string]$RunId = "pre-fix"
    )

    #region agent log
    try {
        $entry = @{
            sessionId    = "079b97"
            runId        = $RunId
            hypothesisId = $HypothesisId
            location     = $Location
            message      = $Message
            data         = $Data
            timestamp    = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
        }
        Add-Content -Path (Get-PipelineDebugLogPath) -Value ($entry | ConvertTo-Json -Compress) -Encoding UTF8
    } catch { }
    #endregion
}

function Invoke-PipelineDeployStatusUpdate {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,
        [hashtable]$Updates = @{},
        [string]$EventType = "",
        [string]$EventMessage = "",
        [string]$EventSha = "",
        [string]$EventSubject = "",
        [string]$EventAuthor = "",
        [string]$EventRunId = "",
        [string]$EventPreviousSha = "",
        [string]$EventStartedAt = "",
        [string]$EventFinishedAt = "",
        [int]$EventDurationSeconds = -1,
        [string]$EventResult = "",
        [string]$EventFailedStep = "",
        [string]$DebugRunId = "pre-fix"
    )

    $supportsExtended = Test-PipelineDeployStatusSupportsExtendedEvents
    Write-PipelineDebugLog -HypothesisId "A" -Location "deploy_status_bridge.ps1:Invoke-PipelineDeployStatusUpdate" `
        -Message "deploy status update" -RunId $DebugRunId -Data @{
            environment      = $Environment
            eventType        = $EventType
            supportsExtended = $supportsExtended
            hasEventSha      = [bool]$EventSha
        }

    $params = @{
        Environment = $Environment
        Updates     = $Updates
    }
    if ($EventType) {
        $params.EventType = $EventType
        $params.EventMessage = $EventMessage
    }

    if ($supportsExtended) {
        if ($EventSha) { $params.EventSha = $EventSha }
        if ($EventSubject) { $params.EventSubject = $EventSubject }
        if ($EventAuthor) { $params.EventAuthor = $EventAuthor }
        if ($EventRunId) { $params.EventRunId = $EventRunId }
        if ($EventPreviousSha) { $params.EventPreviousSha = $EventPreviousSha }
        if ($EventStartedAt) { $params.EventStartedAt = $EventStartedAt }
        if ($EventFinishedAt) { $params.EventFinishedAt = $EventFinishedAt }
        if ($EventDurationSeconds -ge 0) { $params.EventDurationSeconds = $EventDurationSeconds }
        if ($EventResult) { $params.EventResult = $EventResult }
        if ($EventFailedStep) { $params.EventFailedStep = $EventFailedStep }
    }

    Update-DeployStatus @params
}
