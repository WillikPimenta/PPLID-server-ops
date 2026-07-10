$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "watch_all.ps1")
exit $LASTEXITCODE
