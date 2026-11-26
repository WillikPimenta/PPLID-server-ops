[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('prepare','activate','pipeline','promote','rollback','recover','status','cancel','shutdown','workers-start','workers-stop')][string]$Action,
    [Parameter(Mandatory = $true)][ValidateSet('MAIN','DEV','HOM')][string]$Environment,
    [string]$Sha = '',
    [string]$RunId = '',
    [string]$MachineConfig = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'lib\paths.ps1')
if (-not $MachineConfig) { $MachineConfig = Get-PplidMachineConfigPath }
$machine = Get-Content -LiteralPath $MachineConfig -Raw -Encoding UTF8 | ConvertFrom-Json
$config = Get-Content -LiteralPath $machine.envConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$python = $config.$Environment.blueGreen.pythonPath
if (-not $python -or -not (Test-Path -LiteralPath $python)) { throw 'blueGreen.pythonPath ausente/invalido' }
$opsRoot = Split-Path $PSScriptRoot -Parent
Push-Location $opsRoot
try {
    $arguments = @('-m', 'deploy.bluegreen.cli', $Action, '--machine-config', $MachineConfig, '--environment', $Environment)
    if ($Sha) { $arguments += @('--sha', $Sha) }
    if ($RunId) { $arguments += @('--run-id', $RunId) }
    & $python @arguments
    $code = $LASTEXITCODE
} finally { Pop-Location }
exit $code
