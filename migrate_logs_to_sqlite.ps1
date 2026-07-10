param(
    [string]$BaseDir = "C:\PPLID",
    [switch]$DryRun,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\ops_store.ps1")

$deployRoot = Join-Path $BaseDir "deploy"

Write-Host "=== Migracao de logs de arquivo para SQLite ==="
Initialize-OpsStore

if ($DryRun) {
    $count = 0
    foreach ($env in @("MAIN", "DEV", "HOM")) {
        $runsDir = Join-Path $deployRoot "$env\logs\runs"
        if (-not (Test-Path $runsDir)) { continue }
        Get-ChildItem $runsDir -Directory | ForEach-Object {
            foreach ($logName in @("pipeline.log", "build.log", "validate.log", "promote.log", "rollback.log")) {
                $path = Join-Path $_.FullName $logName
                if (Test-Path $path) {
                    $count += (Get-Content $path -Encoding UTF8 -ErrorAction SilentlyContinue).Count
                }
            }
        }
    }
    Write-Host "Total: $count linhas (dry-run - nenhuma escrita)"
    exit 0
}

$python = Get-OpsStorePython
$args = @(
    $script:OpsStoreScript,
    "--db", $script:OpsStoreDbPath,
    "import-legacy-runs",
    "--deploy-root", $deployRoot
)
if ($Force) { $args += "--force" }

$result = & $python @args
Write-Host "Resultado: $result"
