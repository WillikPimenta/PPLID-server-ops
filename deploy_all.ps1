$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "bootstrap_all.ps1")
exit $LASTEXITCODE
