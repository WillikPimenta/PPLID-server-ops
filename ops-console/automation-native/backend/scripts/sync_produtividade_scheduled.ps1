# Agenda a sincronização automática do relatório de produtividade HxH.
# Uso (PowerShell como administrador):
#   .\backend\scripts\sync_produtividade_scheduled.ps1 -BackendDir "C:\Users\c92928a\PPLID\backend"
#
# Cria/atualiza tarefa "PPLID Sync Produtividade" a cada 15 minutos.

param(
    [string]$BackendDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$TaskName = "PPLID Sync Produtividade",
    [int]$IntervalMinutes = 15
)

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    throw "Python não encontrado no PATH."
}

$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument "manage.py sync_produtividade --trigger system" `
    -WorkingDirectory $BackendDir

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) -RepetitionDuration ([TimeSpan]::MaxValue)

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "Tarefa '$TaskName' registrada. Intervalo: $IntervalMinutes min."
Write-Host "Comando: python manage.py sync_produtividade --trigger system"
Write-Host "Diretório: $BackendDir"
