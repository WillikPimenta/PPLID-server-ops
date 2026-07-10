param(
    [ValidateSet("ALL", "MAIN", "DEV", "HOM")]
    [string]$Environment = "ALL",

    [switch]$SkipOpsConsole
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\paths.ps1")
. (Join-Path $PSScriptRoot "lib\port_utils.ps1")

$results = @()
$failed = 0

function Add-CheckResult {
    param(
        [string]$Component,
        [string]$EnvironmentName,
        [bool]$Ok,
        [string]$Detail = ""
    )

    $script:results += [PSCustomObject]@{
        Component   = $Component
        Environment = $EnvironmentName
        Status      = if ($Ok) { "OK" } else { "FAIL" }
        Detail      = $Detail
    }
    if (-not $Ok) {
        $script:failed++
    }
}

function Test-PostgresPort {
    return Test-TcpPortOpen -HostName 127.0.0.1 -Port 5432 -TimeoutMs 2000
}

function Test-BackendHealth {
    param(
        [string]$EnvironmentName,
        [int]$BackendPort
    )

    $url = "http://127.0.0.1:$BackendPort/api/v1/health/"
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
        if ($response.StatusCode -ne 200) {
            return @{ Ok = $false; Detail = "HTTP $($response.StatusCode)" }
        }

        $json = $response.Content | ConvertFrom-Json
        if ($json.database -ne "ok") {
            return @{ Ok = $false; Detail = "database=$($json.database)" }
        }

        return @{ Ok = $true; Detail = "version=$($json.version)" }
    } catch {
        return @{ Ok = $false; Detail = $_.Exception.Message }
    }
}

function Test-FrontendHttp {
    param([int]$FrontendPort)

    $url = "http://127.0.0.1:$FrontendPort/"
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
        if ($response.StatusCode -ge 500) {
            return @{ Ok = $false; Detail = "HTTP $($response.StatusCode)" }
        }
        return @{ Ok = $true; Detail = "HTTP $($response.StatusCode)" }
    } catch {
        return @{ Ok = $false; Detail = $_.Exception.Message }
    }
}

function Get-EnvPorts {
    param([string]$EnvironmentName)

    $configPath = Get-PplidEnvConfigPath -ScriptRoot $PSScriptRoot
    if (-not (Test-Path $configPath)) {
        throw "Config nao encontrada: $configPath"
    }

    $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
    $envCfg = $cfg.$EnvironmentName
    return @{
        BackendPort  = [int]$envCfg.backendPort
        FrontendPort = [int]$envCfg.frontendPort
    }
}

$envList = if ($Environment -eq "ALL") { @("MAIN", "DEV", "HOM") } else { @($Environment) }

Write-Host "=== PPLID Stack Verification ==="
Write-Host ""

$pgOk = Test-PostgresPort
Add-CheckResult -Component "PostgreSQL" -EnvironmentName "-" -Ok $pgOk -Detail $(if ($pgOk) { "TCP 5432" } else { "porta 5432 indisponivel" })

foreach ($env in $envList) {
    try {
        $ports = Get-EnvPorts -EnvironmentName $env
    } catch {
        Add-CheckResult -Component "Config" -EnvironmentName $env -Ok $false -Detail $_.Exception.Message
        continue
    }

    $backend = Test-BackendHealth -EnvironmentName $env -BackendPort $ports.BackendPort
    Add-CheckResult -Component "Backend" -EnvironmentName $env -Ok $backend.Ok -Detail $backend.Detail

    $frontend = Test-FrontendHttp -FrontendPort $ports.FrontendPort
    Add-CheckResult -Component "Frontend" -EnvironmentName $env -Ok $frontend.Ok -Detail $frontend.Detail
}

if (-not $SkipOpsConsole) {
    $consolePort = 5190
    $envConfig = Get-PplidEnvConfigPath -ScriptRoot $PSScriptRoot
    if (Test-Path $envConfig) {
        try {
            $cfg = Get-Content $envConfig -Raw | ConvertFrom-Json
            if ($cfg.opsConsolePort) {
                $consolePort = [int]$cfg.opsConsolePort
            }
        } catch {
            # usa default
        }
    }

    $consoleUrl = "http://127.0.0.1:$consolePort/api/v1/auth/status"
    try {
        $response = Invoke-WebRequest -Uri $consoleUrl -UseBasicParsing -TimeoutSec 5
        $ok = ($response.StatusCode -eq 200)
        Add-CheckResult -Component "OpsConsole" -EnvironmentName "-" -Ok $ok -Detail "HTTP $($response.StatusCode) :$consolePort"
    } catch {
        Add-CheckResult -Component "OpsConsole" -EnvironmentName "-" -Ok $false -Detail $_.Exception.Message
    }
}

$results | Format-Table -AutoSize Component, Environment, Status, Detail

Write-Host ""
if ($failed -eq 0) {
    Write-Host "Todos os checks passaram."
    exit 0
}

Write-Host "$failed check(s) falharam."
exit 1
