$script:OpsStoreScript = Join-Path $PSScriptRoot "ops_store.py"
$script:OpsStoreDbPath = $null

function Get-OpsStoreDbPath {
    if ($script:OpsStoreDbPath) { return $script:OpsStoreDbPath }
    $cfgPath = "C:\PPLID\machine.config.json"
    if (Test-Path $cfgPath) {
        try {
            $raw = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($raw.opsStore -and $raw.opsStore.path) {
                $script:OpsStoreDbPath = [string]$raw.opsStore.path
                return $script:OpsStoreDbPath
            }
        } catch { }
    }
    $script:OpsStoreDbPath = "C:\PPLID\ops\data\ops-store.db"
    return $script:OpsStoreDbPath
}

function Get-OpsStorePython {
    $candidates = @(
        "C:\PPLID\deploy\DEV\current\backend\.venv\Scripts\python.exe"
        (Join-Path $env:ProgramFiles "Python312\python.exe")
        (Join-Path $env:LocalAppData "Programs\Python\Python312\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "Python nao encontrado para ops_store."
}

function Invoke-OpsStoreCli {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Args
    )

    $dbPath = Get-OpsStoreDbPath
    if (-not (Test-Path $dbPath)) {
        $dbDir = Split-Path $dbPath -Parent
        if (-not (Test-Path $dbDir)) {
            New-Item -ItemType Directory -Path $dbDir -Force | Out-Null
        }
        & (Get-OpsStorePython) $script:OpsStoreScript --db $dbPath init | Out-Null
    }

    $python = Get-OpsStorePython
    $allArgs = @($script:OpsStoreScript, "--db", $dbPath) + $Args
    & $python @allArgs
    if ($LASTEXITCODE -ne 0) {
        throw "ops_store falhou (exit $LASTEXITCODE): $($Args -join ' ')"
    }
}

function Initialize-OpsStore {
    Invoke-OpsStoreCli @("init")
}

function Add-OpsDeployLogLine {
    param(
        [ValidateSet("MAIN", "DEV", "HOM")][string]$Environment,
        [string]$RunId,
        [string]$LogName,
        [string]$Level,
        [string]$Message
    )

    Invoke-OpsStoreCli @(
        "append-deploy-log",
        "--env", $Environment,
        "--run-id", $RunId,
        "--log-name", $LogName,
        "--level", $Level,
        "--message", $Message
    ) | Out-Null
}

function Add-OpsDeployLogLines {
    param(
        [ValidateSet("MAIN", "DEV", "HOM")][string]$Environment,
        [string]$RunId,
        [string]$LogName,
        [array]$Entries
    )

    if (-not $Entries -or $Entries.Count -eq 0) { return }
    $wrapped = @($Entries)
    if ($wrapped.Count -eq 1) {
        $json = '[' + (ConvertTo-Json -InputObject $wrapped[0] -Depth 6 -Compress) + ']'
    } else {
        $json = ConvertTo-Json -InputObject $wrapped -Depth 6 -Compress
    }
    $tempFile = Join-Path $env:TEMP ("pplid-log-batch-{0}.json" -f ([guid]::NewGuid().ToString("N").Substring(0, 8)))
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($tempFile, $json, $utf8)
    try {
        Invoke-OpsStoreCli @(
            "append-deploy-log-batch",
            "--env", $Environment,
            "--run-id", $RunId,
            "--log-name", $LogName,
            "--entries-file", $tempFile
        ) | Out-Null
    } finally {
        Remove-Item $tempFile -Force -ErrorAction SilentlyContinue
    }
}

function Save-OpsDeploySteps {
    param(
        [ValidateSet("MAIN", "DEV", "HOM")][string]$Environment,
        [string]$RunId,
        [array]$Steps
    )

    $wrapped = @($Steps)
    if ($wrapped.Count -eq 1) {
        $json = '[' + (ConvertTo-Json -InputObject $wrapped[0] -Depth 8 -Compress) + ']'
    } else {
        $json = ConvertTo-Json -InputObject $wrapped -Depth 8 -Compress
    }
    $tempFile = Join-Path $env:TEMP "pplid-steps-$RunId.json"
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($tempFile, $json, $utf8)
    try {
        $python = Get-OpsStorePython
        $dbPath = Get-OpsStoreDbPath
        & $python $script:OpsStoreScript --db $dbPath save-steps `
            --env $Environment --run-id $RunId `
            --steps-file $tempFile
        if ($LASTEXITCODE -ne 0) {
            throw "save-steps exit $LASTEXITCODE"
        }
    } finally {
        Remove-Item $tempFile -Force -ErrorAction SilentlyContinue
    }
}

function Set-OpsDeployRunSummary {
    param(
        [ValidateSet("MAIN", "DEV", "HOM")][string]$Environment,
        [string]$RunId,
        [hashtable]$Summary
    )

    $tempFile = Join-Path $env:TEMP "pplid-summary-$RunId.json"
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($tempFile, ($Summary | ConvertTo-Json -Depth 8 -Compress), $utf8)
    try {
        $python = Get-OpsStorePython
        $dbPath = Get-OpsStoreDbPath
        $cliArgs = @(
            $script:OpsStoreScript, "--db", $dbPath,
            "upsert-run",
            "--env", $Environment,
            "--run-id", $RunId,
            "--summary-file", $tempFile
        )
        if ($Summary.toSha) { $cliArgs += @("--target-sha", [string]$Summary.toSha) }
        if ($Summary.result) { $cliArgs += @("--result", [string]$Summary.result) }
        if ($Summary.finishedAt) { $cliArgs += @("--finished-at", [string]$Summary.finishedAt) }
        if ($Summary.failedStep) { $cliArgs += @("--failed-step", [string]$Summary.failedStep) }
        & $python @cliArgs
        if ($LASTEXITCODE -ne 0) {
            throw "upsert-run exit $LASTEXITCODE"
        }
    } finally {
        Remove-Item $tempFile -Force -ErrorAction SilentlyContinue
    }
}

function Add-OpsServiceLogLine {
    param(
        [ValidateSet("MAIN", "DEV", "HOM")][string]$Environment,
        [string]$Service,
        [string]$Stream,
        [string]$Line
    )

    Invoke-OpsStoreCli @(
        "append-service-log",
        "--env", $Environment,
        "--service", $Service,
        "--stream", $Stream,
        "--line", $Line
    ) | Out-Null
}

function Write-OpsEnvLog {
    <#
    .SYNOPSIS
      Append ops/orchestration summary lines to SQLite (service_log_lines).
      Replaces fragile Add-Content to PPLID_{ENV}.log.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,
        [Parameter(Mandatory = $true)]
        [ValidateSet("watcher", "pipeline", "bootstrap", "lock", "recover", "app", "orphan-bots")]
        [string]$Service,
        [Parameter(Mandatory = $true)]
        [string]$Message,
        [string]$Stream = "summary"
    )

    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $Message"
    try {
        Add-OpsServiceLogLine -Environment $Environment -Service $Service -Stream $Stream -Line $line
    } catch {
        # Never break deploy/watch because of logging
        Write-Verbose "Write-OpsEnvLog falhou: $($_.Exception.Message)"
    }
}
