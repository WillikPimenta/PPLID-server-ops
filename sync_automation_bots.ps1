param(
    [Parameter(Mandatory = $true)]
    [string]$PplidDir,
    [string]$Destination = ""
)

$ErrorActionPreference = "Stop"
$source = Join-Path $PplidDir "automacoes"
if (-not (Test-Path (Join-Path $source "app"))) { throw "Pacote de bots nao encontrado em $source" }
if (-not $Destination) { $Destination = Join-Path $PSScriptRoot "ops-console\automation-native" }
$target = Join-Path $Destination "automacoes"
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
if (Test-Path $target) { Remove-Item -LiteralPath $target -Recurse -Force }
$null = New-Item -ItemType Directory -Path $target -Force
& robocopy $source $target /E /XD .pytest_cache .ruff_cache __pycache__ .git data /XF *.pyc *.pyo /NFL /NDL /NJH /NJS /NP
if ($LASTEXITCODE -ge 8) { throw "Falha ao sincronizar os bots (robocopy=$LASTEXITCODE)" }
Write-Host "Bots sincronizados para $target"
Write-Host "O codigo original em $source foi preservado."
$backendSource = Join-Path $PplidDir "backend"
$backendTarget = Join-Path $Destination "backend"
if (Test-Path $backendSource) {
    New-Item -ItemType Directory -Path $backendTarget -Force | Out-Null
    & robocopy $backendSource $backendTarget /E /XD .pytest_cache .ruff_cache __pycache__ .git media staticfiles node_modules /XF *.pyc *.pyo *.log /NFL /NDL /NJH /NJS /NP
    if ($LASTEXITCODE -ge 8) { throw "Falha ao sincronizar o backend nativo (robocopy=$LASTEXITCODE)" }
    Write-Host "Backend de suporte sincronizado para $backendTarget"
}
