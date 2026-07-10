param(
    [int]$IntervalMinutes = 1,
    [switch]$Uninstall,
    [switch]$SkipSystemAccount
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "lib\paths.ps1")

$TaskName = "PPLID-GitHub-Sync"
$UpdateScript = Join-Path $PSScriptRoot "update_all.ps1"
$PowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

function Test-IsAdministrator {
    $current = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($current)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if ($Uninstall) {
    cmd /c "schtasks /Delete /TN `"$TaskName`" /F" 2>$null
    Write-Host "Task '$TaskName' removida (se existia)."
    exit 0
}

if (-not (Test-Path $UpdateScript)) {
    throw "Script nao encontrado: $UpdateScript"
}

$useSystem = (-not $SkipSystemAccount) -and (Test-IsAdministrator)
if (-not $useSystem -and -not $SkipSystemAccount) {
    Write-Warning "Execute como Administrador para registrar a task como SYSTEM."
    Write-Warning "Registrando para o usuario atual ($env:USERNAME)..."
}

cmd /c "schtasks /Delete /TN `"$TaskName`" /F" 2>$null

$taskAction = "$PowerShell -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$UpdateScript`""
if ($useSystem) {
    $result = cmd /c "schtasks /Create /TN `"$TaskName`" /TR `"$taskAction`" /SC MINUTE /MO $IntervalMinutes /RU SYSTEM /RP /RL HIGHEST /F" 2>&1
} else {
    $result = cmd /c "schtasks /Create /TN `"$TaskName`" /TR `"$taskAction`" /SC MINUTE /MO $IntervalMinutes /F" 2>&1
}

if ($LASTEXITCODE -ne 0) {
    throw "Falha ao criar task: $result"
}

Write-Host "Task '$TaskName' registrada (intervalo: $IntervalMinutes min)."
Write-Host "Acao: $taskAction"
Write-Host $result
