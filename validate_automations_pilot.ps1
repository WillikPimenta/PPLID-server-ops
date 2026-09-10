param(
    [switch]$Local,
    [switch]$Server
)

$ErrorActionPreference = "Stop"
$scriptRoot = $PSScriptRoot
$opsConsole = Join-Path $scriptRoot "ops-console"
$python = Join-Path $opsConsole ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

$args = @((Join-Path $opsConsole "tools\validate_automations_pilot.py"))
if ($Server) { $args += "--server" } else { $args += "--local" }

& $python @args
exit $LASTEXITCODE
