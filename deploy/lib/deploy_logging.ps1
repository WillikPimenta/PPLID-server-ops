. (Join-Path $PSScriptRoot "deploy_paths.ps1")
. (Join-Path $PSScriptRoot "run_log.ps1")

$opsStorePs1 = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) "lib\ops_store.ps1"
if (Test-Path $opsStorePs1) {
    . $opsStorePs1
    $script:OpsStoreEnabled = $true
} else {
    $script:OpsStoreEnabled = $false
}

$script:MirrorFileLogs = $true

function Get-OpsStoreConfig {
    $cfgPath = "C:\PPLID\machine.config.json"
    if (-not (Test-Path $cfgPath)) { return @{ mirrorFileLogs = $true } }
    try {
        $raw = Get-Content $cfgPath -Raw | ConvertFrom-Json
        $mirror = $true
        if ($raw.opsStore -and $null -ne $raw.opsStore.mirrorFileLogs) {
            $mirror = [bool]$raw.opsStore.mirrorFileLogs
        }
        return @{ mirrorFileLogs = $mirror }
    } catch {
        return @{ mirrorFileLogs = $true }
    }
}

function Add-DeployLogLineToFile {
    param(
        [Parameter(Mandatory = $true)][string]$LogFile,
        [Parameter(Mandatory = $true)][string]$Line
    )

    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Line + [Environment]::NewLine)
    $delays = @(50, 100, 200)
    $lastError = $null
    foreach ($attempt in 0..($delays.Count)) {
        try {
            $stream = [System.IO.File]::Open(
                $LogFile,
                [System.IO.FileMode]::Append,
                [System.IO.FileAccess]::Write,
                [System.IO.FileShare]::ReadWrite
            )
            try {
                $stream.Write($bytes, 0, $bytes.Length)
                $stream.Flush()
            } finally {
                $stream.Dispose()
            }
            return
        } catch {
            $lastError = $_
            if ($attempt -lt $delays.Count) {
                Start-Sleep -Milliseconds $delays[$attempt]
            }
        }
    }
    throw $lastError
}

$script:MirrorFileLogs = (Get-OpsStoreConfig).mirrorFileLogs

$script:DeployLogRedactPatterns = @(
    '(?i)(password|passwd|pwd|secret|token|api[_-]?key|authorization)\s*[=:]\s*\S+'
    '(?i)Bearer\s+\S+'
    '(?i)postgresql://[^\s]+'
    '(?i)mysql://[^\s]+'
    '(?i)SECRET_KEY\s*=\s*\S+'
    '(?i)DATABASE_URL\s*=\s*\S+'
)

function Protect-DeployLogText {
    param([string]$Text)
    if (-not $Text) { return "" }
    $out = $Text
    foreach ($pattern in $script:DeployLogRedactPatterns) {
        $out = [regex]::Replace($out, $pattern, { param($m) ($m.Value -replace '=\s*\S+$', '=***') -replace ':\s*\S+$', ':***' })
    }
    return $out
}

function Write-DeployLogEntry {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,
        [Parameter(Mandatory = $true)]
        [string]$RunId,
        [Parameter(Mandatory = $true)]
        [ValidateSet("INFO", "OK", "WARN", "ERROR", "SUCCESS")]
        [string]$Level,
        [Parameter(Mandatory = $true)]
        [string]$Message,
        [string]$LogName = "pipeline.log"
    )

    $safe = Protect-DeployLogText -Text $Message
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [$Level] $safe"

    $sqliteOk = $false
    if ($script:OpsStoreEnabled) {
        try {
            Add-OpsDeployLogLine -Environment $Environment -RunId $RunId -LogName $LogName -Level $Level -Message $safe
            $sqliteOk = $true
        } catch {
            # SQLite indisponivel — fallback para arquivo abaixo
        }
    }

    if (-not $script:MirrorFileLogs -and $sqliteOk) {
        return
    }

    $runDir = Get-PplidDeployRunDir -Environment $Environment -RunId $RunId
    if (-not (Test-Path $runDir)) {
        New-Item -ItemType Directory -Path $runDir -Force | Out-Null
    }
    $logFile = Join-Path $runDir $LogName
    Add-DeployLogLineToFile -LogFile $logFile -Line $line
}

function Write-DeployLogInfo {
    param([ValidateSet("MAIN", "DEV", "HOM")][string]$Environment, [string]$RunId, [string]$Message, [string]$LogName = "pipeline.log")
    Write-DeployLogEntry -Environment $Environment -RunId $RunId -Level INFO -Message $Message -LogName $LogName
}
function Write-DeployLogOk {
    param([ValidateSet("MAIN", "DEV", "HOM")][string]$Environment, [string]$RunId, [string]$Message, [string]$LogName = "pipeline.log")
    Write-DeployLogEntry -Environment $Environment -RunId $RunId -Level OK -Message $Message -LogName $LogName
}
function Write-DeployLogWarn {
    param([ValidateSet("MAIN", "DEV", "HOM")][string]$Environment, [string]$RunId, [string]$Message, [string]$LogName = "pipeline.log")
    Write-DeployLogEntry -Environment $Environment -RunId $RunId -Level WARN -Message $Message -LogName $LogName
}
function Write-DeployLogError {
    param([ValidateSet("MAIN", "DEV", "HOM")][string]$Environment, [string]$RunId, [string]$Message, [string]$LogName = "pipeline.log")
    Write-DeployLogEntry -Environment $Environment -RunId $RunId -Level ERROR -Message $Message -LogName $LogName
}
function Write-DeployLogSuccess {
    param([ValidateSet("MAIN", "DEV", "HOM")][string]$Environment, [string]$RunId, [string]$Message, [string]$LogName = "pipeline.log")
    Write-DeployLogEntry -Environment $Environment -RunId $RunId -Level SUCCESS -Message $Message -LogName $LogName
}

function Get-DeployStepsPath {
    param([ValidateSet("MAIN", "DEV", "HOM")][string]$Environment, [string]$RunId)
    $runDir = Get-PplidDeployRunDir -Environment $Environment -RunId $RunId
    return Join-Path $runDir "steps.json"
}

function Get-DeployRunSummaryPath {
    param([ValidateSet("MAIN", "DEV", "HOM")][string]$Environment, [string]$RunId)
    $runDir = Get-PplidDeployRunDir -Environment $Environment -RunId $RunId
    return Join-Path $runDir "run-summary.json"
}

function Ensure-DeployRunDir {
    param(
        [ValidateSet("MAIN", "DEV", "HOM")][string]$Environment,
        [string]$RunId
    )
    $runDir = Get-PplidDeployRunDir -Environment $Environment -RunId $RunId
    if (-not (Test-Path $runDir)) {
        New-Item -ItemType Directory -Path $runDir -Force | Out-Null
    }
    return $runDir
}

function Save-DeployStepsFile {
    param(
        [string]$Path,
        [array]$Steps,
        [ValidateSet("MAIN", "DEV", "HOM")][string]$Environment = "",
        [string]$RunId = ""
    )

    if ($Environment -and $RunId -and $script:OpsStoreEnabled) {
        try {
            Save-OpsDeploySteps -Environment $Environment -RunId $RunId -Steps $Steps
        } catch { }
    }

    # steps.json e metadata — sempre persistir em arquivo (nao e log de alto volume)
    $dir = Split-Path $Path -Parent
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Path, ($Steps | ConvertTo-Json -Depth 6), $utf8)
}

function Initialize-DeploySteps {
    param(
        [ValidateSet("MAIN", "DEV", "HOM")][string]$Environment,
        [string]$RunId
    )

    $defs = @(
        @{ id = "prepare";          label = "Preparacao";           logFile = "pipeline.log"; phase = "building" }
        @{ id = "git_fetch";          label = "Git fetch/pull";       logFile = "build.log";    phase = "building" }
        @{ id = "deps_backend";       label = "Instalacao dependencias"; logFile = "build.log"; phase = "building" }
        @{ id = "build_backend";      label = "Build backend";        logFile = "build.log";    phase = "building" }
        @{ id = "build_frontend";     label = "Build frontend";       logFile = "build.log";    phase = "building" }
        @{ id = "validate";           label = "Validacao";            logFile = "validate.log"; phase = "validating" }
        @{ id = "restart_services";   label = "Reinicio servicos";    logFile = "promote.log";  phase = "promoting" }
        @{ id = "health_check";       label = "Health check";         logFile = "promote.log";  phase = "promoting" }
        @{ id = "publish_done";       label = "Publicacao concluida"; logFile = "promote.log";  phase = "promoting" }
    )

    $steps = foreach ($d in $defs) {
        @{
            id          = $d.id
            label       = $d.label
            phase       = $d.phase
            logFile     = $d.logFile
            status      = "pending"
            startedAt   = $null
            finishedAt  = $null
            durationSec = $null
            error       = $null
        }
    }

    Save-DeployStepsFile -Path (Get-DeployStepsPath -Environment $Environment -RunId $RunId) -Steps $steps -Environment $Environment -RunId $RunId
    return $steps
}

function Get-DeploySteps {
    param([ValidateSet("MAIN", "DEV", "HOM")][string]$Environment, [string]$RunId)
    $path = Get-DeployStepsPath -Environment $Environment -RunId $RunId
    if (-not (Test-Path $path)) {
        return @(Initialize-DeploySteps -Environment $Environment -RunId $RunId)
    }
    try {
        $raw = Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json
        return @($raw)
    } catch {
        return @(Initialize-DeploySteps -Environment $Environment -RunId $RunId)
    }
}

function Start-DeployStep {
    param(
        [ValidateSet("MAIN", "DEV", "HOM")][string]$Environment,
        [string]$RunId,
        [Parameter(Mandatory = $true)][string]$StepId
    )

    $steps = @(Get-DeploySteps -Environment $Environment -RunId $RunId)
    $now = (Get-Date).ToString("o")
    $updated = foreach ($s in $steps) {
        $h = @{}
        $s.PSObject.Properties | ForEach-Object { $h[$_.Name] = $_.Value }
        if ($h.id -eq $StepId) {
            $h.status = "running"
            $h.startedAt = $now
            $h.error = $null
        }
        $h
    }
    Save-DeployStepsFile -Path (Get-DeployStepsPath -Environment $Environment -RunId $RunId) -Steps $updated -Environment $Environment -RunId $RunId
}

function Complete-DeployStep {
    param(
        [ValidateSet("MAIN", "DEV", "HOM")][string]$Environment,
        [string]$RunId,
        [Parameter(Mandatory = $true)][string]$StepId,
        [ValidateSet("success", "warning", "error", "skipped")][string]$Status = "success",
        [string]$ErrorMessage = ""
    )

    $steps = @(Get-DeploySteps -Environment $Environment -RunId $RunId)
    $now = (Get-Date).ToString("o")
    $updated = foreach ($s in $steps) {
        $h = @{}
        $s.PSObject.Properties | ForEach-Object { $h[$_.Name] = $_.Value }
        if ($h.id -eq $StepId) {
            $h.status = $Status
            if (-not $h.startedAt) { $h.startedAt = $now }
            $h.finishedAt = $now
            if ($h.startedAt) {
                try {
                    $sec = [int]([datetime]::Parse($h.finishedAt) - [datetime]::Parse($h.startedAt)).TotalSeconds
                    if ($sec -lt 0) { $sec = 0 }
                    $h.durationSec = $sec
                } catch { $h.durationSec = $null }
            }
            if ($ErrorMessage) { $h.error = (Protect-DeployLogText -Text $ErrorMessage) }
        }
        $h
    }
    Save-DeployStepsFile -Path (Get-DeployStepsPath -Environment $Environment -RunId $RunId) -Steps $updated -Environment $Environment -RunId $RunId
}

function Skip-RemainingDeploySteps {
    param(
        [ValidateSet("MAIN", "DEV", "HOM")][string]$Environment,
        [string]$RunId,
        [string]$AfterStepId = ""
    )

    $steps = @(Get-DeploySteps -Environment $Environment -RunId $RunId)
    $skipRest = [string]::IsNullOrWhiteSpace($AfterStepId)
    $updated = foreach ($s in $steps) {
        $h = @{}
        $s.PSObject.Properties | ForEach-Object { $h[$_.Name] = $_.Value }
        if ($h.id -eq $AfterStepId) { $skipRest = $true }
        if ($skipRest -and $h.status -in @("pending", "running")) {
            $h.status = "skipped"
            $h.finishedAt = (Get-Date).ToString("o")
        }
        $h
    }
    Save-DeployStepsFile -Path (Get-DeployStepsPath -Environment $Environment -RunId $RunId) -Steps $updated -Environment $Environment -RunId $RunId
}

function Update-DeployManifest {
    param(
        [ValidateSet("MAIN", "DEV", "HOM")][string]$Environment,
        [string]$RunId,
        [hashtable]$Updates
    )

    $runDir = Ensure-DeployRunDir -Environment $Environment -RunId $RunId
    $manifestPath = Join-Path $runDir "manifest.json"
    $manifest = @{}
    if (Test-Path $manifestPath) {
        try {
            $raw = Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($raw) {
                $raw.PSObject.Properties | ForEach-Object { $manifest[$_.Name] = $_.Value }
            }
        } catch {
            $manifest = @{}
        }
    }
    foreach ($key in $Updates.Keys) {
        $manifest[$key] = $Updates[$key]
    }
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 6), $utf8)
}

function Update-PplidRunsIndex {
    param(
        [ValidateSet("MAIN", "DEV", "HOM")][string]$Environment,
        [string]$RunId,
        [string]$ToSha = "",
        [string]$Result = "",
        [string]$FinishedAt = ""
    )

    if (-not $ToSha) { return }
    $paths = Get-PplidDeployEnvPaths -Environment $Environment
    $indexPath = Join-Path $paths.Logs "runs-index.json"
    $entries = @()
    if (Test-Path $indexPath) {
        try {
            $raw = Get-Content $indexPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($raw.entries) { $entries = @($raw.entries) }
        } catch { }
    }
    $entries = @($entries | Where-Object { $_.runId -ne $RunId })
    $entry = @{
        runId      = $RunId
        toSha      = $ToSha
        result     = $Result
        finishedAt = $FinishedAt
    }
    $entries = ,$entry + $entries
    if ($entries.Count -gt 200) {
        $entries = $entries[0..199]
    }
    $payload = @{
        updatedAt = (Get-Date).ToString("o")
        entries   = $entries
    }
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($indexPath, ($payload | ConvertTo-Json -Depth 6), $utf8)
}

function Write-DeployRunSummary {
    param(
        [ValidateSet("MAIN", "DEV", "HOM")][string]$Environment,
        [string]$RunId,
        [hashtable]$Summary
    )

    $path = Get-DeployRunSummaryPath -Environment $Environment -RunId $RunId
    $dir = Split-Path $path -Parent
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    if (Test-Path $path) {
        try {
            $existing = Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($existing.errorDetail -and -not $Summary.ContainsKey("errorDetail")) {
                $Summary.errorDetail = $existing.errorDetail
            }
        } catch { }
    }
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($path, ($Summary | ConvertTo-Json -Depth 8), $utf8)

    if ($script:OpsStoreEnabled) {
        try {
            Set-OpsDeployRunSummary -Environment $Environment -RunId $RunId -Summary $Summary
        } catch { }
    }

    Update-PplidRunsIndex -Environment $Environment -RunId $RunId `
        -ToSha ([string]$Summary.toSha) `
        -Result ([string]$Summary.result) `
        -FinishedAt ([string]$Summary.finishedAt)
}

function Save-DeployErrorDetail {
    param(
        [ValidateSet("MAIN", "DEV", "HOM")][string]$Environment,
        [string]$RunId,
        [hashtable]$ErrorDetail,
        [string]$FailedStep = ""
    )

    $path = Get-DeployRunSummaryPath -Environment $Environment -RunId $RunId
    $summary = @{}
    if (Test-Path $path) {
        try {
            $raw = Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json
            $raw.PSObject.Properties | ForEach-Object { $summary[$_.Name] = $_.Value }
        } catch { }
    }
    $summary.errorDetail = $ErrorDetail
    if ($FailedStep) { $summary.failedStep = $FailedStep }
    if (-not $summary.runId) { $summary.runId = $RunId }
    if (-not $summary.environment) { $summary.environment = $Environment }
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($path, ($summary | ConvertTo-Json -Depth 8), $utf8)
}

function Get-FailedDeployStepId {
    param([ValidateSet("MAIN", "DEV", "HOM")][string]$Environment, [string]$RunId)
    foreach ($s in @(Get-DeploySteps -Environment $Environment -RunId $RunId)) {
        if ($s.status -eq "error") { return [string]$s.id }
    }
    return ""
}
