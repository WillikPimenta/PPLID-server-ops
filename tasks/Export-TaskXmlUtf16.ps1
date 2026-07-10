# Regrava XMLs em UTF-16 LE com BOM. Prefira Repair-TaskXmls.ps1 se a importacao falhar.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Repair-TaskXmls.ps1")
