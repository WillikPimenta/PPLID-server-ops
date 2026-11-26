. (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'lib\paths.ps1')

function Test-PplidBlueGreen {
    param([Parameter(Mandatory = $true)][string]$Environment)
    $path = Get-PplidEnvConfigPath
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    $config = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    $strategy = $config.$Environment.deployStrategy
    if ($strategy -and $strategy -notin @('legacy', 'blueGreen')) { throw "Estrategia desconhecida: $strategy" }
    # Existing journal protects the proxy even if someone edits the feature flag.
    $state = Join-Path (Get-PplidBaseDir) "deploy\$Environment\bluegreen\state.json"
    if ((Test-Path -LiteralPath $state) -and $strategy -ne 'blueGreen') {
        throw 'Estado blue-green existente; use o runbook antes de voltar ao legado.'
    }
    return ($strategy -eq 'blueGreen')
}

function Invoke-PplidBlueGreen {
    param([Parameter(Mandatory = $true)][string]$Action,
          [Parameter(Mandatory = $true)][string]$Environment,
          [string]$Sha = '', [string]$RunId = '')
    $scriptPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'bluegreen.ps1'
    # A child PowerShell would deadlock against a mutex already held by this
    # pipeline. Python owns the same named mutex in a separate process, so the
    # pipeline must dispatch blue-green BEFORE acquiring its legacy mutex.
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $scriptPath, '-Action', $Action, '-Environment', $Environment)
    if ($Sha) { $arguments += @('-Sha', $Sha) }
    if ($RunId) { $arguments += @('-RunId', $RunId) }
    & powershell.exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "Blue-green $Action falhou (exit $LASTEXITCODE)" }
}
