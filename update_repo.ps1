param(
    [ValidateSet("MAIN", "DEV", "HOM")]
    [string]$Environment
)

$ErrorActionPreference = "Stop"

if (-not $Environment) {
    throw "Informe -Environment MAIN, DEV ou HOM."
}

& (Join-Path $PSScriptRoot "deploy\watch_github.ps1") -Environment $Environment
exit $LASTEXITCODE
