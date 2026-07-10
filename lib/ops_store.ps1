$script:OpsStoreScript = Join-Path $PSScriptRoot "ops_store.py"
$script:OpsStoreDbPath = "C:\PPLID\ops\data\ops-store.db"

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

    if (-not (Test-Path $script:OpsStoreDbPath)) {
        & (Get-OpsStorePython) $script:OpsStoreScript --db $script:OpsStoreDbPath init | Out-Null
    }

    $python = Get-OpsStorePython
    $allArgs = @($script:OpsStoreScript, "--db", $script:OpsStoreDbPath) + $Args
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

    $escaped = $Message -replace '"', '\"'
    Invoke-OpsStoreCli @(
        "append-deploy-log",
        "--env", $Environment,
        "--run-id", $RunId,
        "--log-name", $LogName,
        "--level", $Level,
        "--message", $Message
    ) | Out-Null
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
        & $python $script:OpsStoreScript --db $script:OpsStoreDbPath save-steps `
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
        $cliArgs = @(
            $script:OpsStoreScript, "--db", $script:OpsStoreDbPath,
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
