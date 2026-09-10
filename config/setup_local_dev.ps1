param(
    [string]$PplidDir = "",
    [switch]$EnableDev,
    [switch]$CreateDevDatabase
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path $PSScriptRoot -Parent
$LocalRoot = Join-Path $RepoRoot ".local"
$ConfigDir = Join-Path $RepoRoot "config"
$EnvPath = Join-Path $ConfigDir "env.config.local.json"
$MachinePath = Join-Path $ConfigDir "machine.config.local.json"

if (-not $PplidDir) {
    $candidate = Join-Path (Split-Path $RepoRoot -Parent) "PPLID"
    if (Test-Path (Join-Path $candidate "backend")) {
        $PplidDir = (Resolve-Path $candidate).Path
    }
}

if (-not $PplidDir -or -not (Test-Path (Join-Path $PplidDir "backend"))) {
    throw "Informe -PplidDir apontando para o checkout local do PPLID (pasta com backend/)."
}

$PplidDir = (Resolve-Path $PplidDir).Path
foreach ($dir in @("repos", "logs", "deploy", "automation-runtime")) {
    New-Item -ItemType Directory -Path (Join-Path $LocalRoot $dir) -Force | Out-Null
}

$machine = [ordered]@{
    baseDir = $LocalRoot
    lanIp = "127.0.0.1"
    envConfigPath = $EnvPath
    opsConsoleDir = Join-Path $RepoRoot "ops-console"
    automationRuntime = @{ root = Join-Path $LocalRoot "automation-runtime" }
    reposDir = Join-Path $LocalRoot "repos"
    deployDir = Join-Path $LocalRoot "deploy"
    automationSourceDir = $PplidDir
    automationOpsDir = Join-Path $RepoRoot "ops-console\automation-native"
}
$machine | ConvertTo-Json -Depth 5 | Set-Content -Path $MachinePath -Encoding UTF8

$examplePath = Join-Path $ConfigDir "env.config.local.example.json"
if (-not (Test-Path $EnvPath) -and (Test-Path $examplePath)) {
    Copy-Item $examplePath $EnvPath
}

$envCfg = Get-Content $EnvPath -Raw | ConvertFrom-Json
$envCfg.MAIN.repoDir = $PplidDir
$envCfg.MAIN.enabled = $true
$envCfg.DEV.enabled = $false
$envCfg.HOM.enabled = $false
$envCfg | ConvertTo-Json -Depth 6 | Set-Content -Path $EnvPath -Encoding UTF8

function Test-PostgresDatabase {
    param([string]$DatabaseName)
    $probe = Join-Path $RepoRoot "ops-console\tools\_probe_local_dbs.py"
    if (-not (Test-Path $probe)) { return $false }
    $list = & python $probe 2>$null
    return ($list -match $DatabaseName)
}

if ($CreateDevDatabase) {
    $createScript = @'
import psycopg
with psycopg.connect(host="127.0.0.1", port=5432, dbname="postgres", user="postgres", password="postgres", autocommit=True) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", ("pplid_dev",))
        if cur.fetchone():
            print("exists")
        else:
            cur.execute('CREATE DATABASE pplid_dev')
            print("created")
'@
    $result = $createScript | python -
    Write-Host "Postgres pplid_dev: $result"
}

$devDbReady = $EnableDev -or (Test-PostgresDatabase -DatabaseName "pplid_dev")
if ($devDbReady) {
    $envCfg = Get-Content $EnvPath -Raw | ConvertFrom-Json
    $envCfg.DEV.enabled = $true
    $envCfg | ConvertTo-Json -Depth 6 | Set-Content -Path $EnvPath -Encoding UTF8
}

function Write-LocalBackendEnv {
    param([string]$Environment, [string]$PostgresDb)
    $shared = Join-Path $LocalRoot "deploy\$Environment\shared"
    New-Item -ItemType Directory -Path (Join-Path $shared "media") -Force | Out-Null
    $backendEnv = Join-Path $shared "backend.env"
    $sourceEnv = Join-Path $PplidDir "backend\.env"
    $lines = @()
    if (Test-Path $sourceEnv) {
        $lines = Get-Content $sourceEnv | Where-Object {
            $_ -notmatch '^\s*#' -and $_ -notmatch '10\.97\.198\.186' -and $_ -notmatch 'monitoramento\.local'
        }
    }
    if (-not $lines) {
        $lines = @(
            "SECRET_KEY=dev-secret-key-change-in-production",
            "DEBUG=True",
            "ALLOWED_HOSTS=localhost,127.0.0.1"
        )
    }
    $map = @{}
    foreach ($line in $lines) {
        if ($line -match '^\s*([^=]+)=(.*)$') {
            $map[$Matches[1].Trim()] = $Matches[2].Trim()
        }
    }
    $map["POSTGRES_DB"] = $PostgresDb
    $map["POSTGRES_HOST"] = "127.0.0.1"
    $map["POSTGRES_PORT"] = "5432"
    if (-not $map["POSTGRES_USER"]) { $map["POSTGRES_USER"] = "postgres" }
    if (-not $map["POSTGRES_PASSWORD"]) { $map["POSTGRES_PASSWORD"] = "postgres" }
    $map["MEDIA_ROOT"] = Join-Path $shared "media"
    ($map.GetEnumerator() | Sort-Object Name | ForEach-Object { "{0}={1}" -f $_.Key, $_.Value }) -join "`n" | Set-Content -Path $backendEnv -Encoding UTF8
}

Write-LocalBackendEnv -Environment "MAIN" -PostgresDb "pplid_main"
if ($devDbReady) {
    Write-LocalBackendEnv -Environment "DEV" -PostgresDb "pplid_dev"
}

$settingsPath = Join-Path $LocalRoot "automation-runtime\settings.json"
if (Test-Path $settingsPath) {
    $settings = Get-Content $settingsPath -Raw | ConvertFrom-Json
} else {
    $settings = [ordered]@{}
}
$targets = @("MAIN")
if ($devDbReady) { $targets += "DEV" }
$settings.targetEnvironment = $targets[0]
$settings.targetEnvironments = $targets
$settings | ConvertTo-Json -Depth 5 | Set-Content -Path $settingsPath -Encoding UTF8

Write-Host "Config local pronta."
Write-Host "  machine: $MachinePath"
Write-Host "  env:     $EnvPath"
Write-Host "  PPLID:   $PplidDir"
Write-Host "  targets: $($targets -join ', ')"
Write-Host "Reinicie: .\\start_ops_console.ps1 -Local -Restart"
