<#
.SYNOPSIS
    Registra task PPLID-Ops-Console (ONLOGON ou ONSTART).

.NOTES
    Uso recomendado sem admin: -SkipSystemAccount -OnLogon (console ao login).
    ONSTART/SYSTEM e legado (boot sem login).
#>
param(
    [switch]$Uninstall,
    [switch]$SkipSystemAccount,
    [switch]$OnLogon
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "lib\paths.ps1")

$TaskName = "PPLID-Ops-Console"
$StartScript = Join-Path $PSScriptRoot "start_ops_console.ps1"
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

if (-not (Test-Path $StartScript)) {
    throw "Script nao encontrado: $StartScript"
}

$useSystem = (-not $SkipSystemAccount) -and (Test-IsAdministrator)
if (-not $useSystem -and -not $SkipSystemAccount) {
    Write-Warning "Execute como Administrador para registrar a task como SYSTEM (boot sem login)."
    Write-Warning "Registrando para o usuario atual ($env:USERNAME)..."
}

cmd /c "schtasks /Delete /TN `"$TaskName`" /F" 2>$null

$schedule = if ($OnLogon) { "ONLOGON" } else { "ONSTART" }
$taskAction = "$PowerShell -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartScript`""
if ($useSystem) {
    $result = cmd /c "schtasks /Create /TN `"$TaskName`" /TR `"$taskAction`" /SC $schedule /RU SYSTEM /RP /RL HIGHEST /F" 2>&1
} else {
    $result = cmd /c "schtasks /Create /TN `"$TaskName`" /TR `"$taskAction`" /SC $schedule /RL HIGHEST /F" 2>&1
}

if ($LASTEXITCODE -ne 0) {
    throw "Falha ao criar task: $result"
}

$triggerLabel = if ($OnLogon) { "ao login do usuario" } else { "inicio no boot" }
Write-Host "Task '$TaskName' registrada ($triggerLabel, /SC $schedule)."
Write-Host $result
