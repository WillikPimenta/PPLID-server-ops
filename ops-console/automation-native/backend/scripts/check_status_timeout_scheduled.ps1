# Agenda o timeout automático de status após fim da escala (opcional).
# O timeout também roda na aplicação via STATUS_TIMEOUT_ENABLED=True (padrão).
# Use este script apenas se preferir Task Scheduler em vez da thread interna.
# Uso (PowerShell como administrador):
#   .\backend\scripts\check_status_timeout_scheduled.ps1 -BackendDir "C:\Users\c91763a\PPLID\PPLID\backend"
#
# Cria/atualiza tarefa "PPLID Check Status Timeout" a cada 5 minutos.

param(
    [string]$BackendDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$TaskName = "PPLID Check Status Timeout",
    [int]$IntervalMinutes = 5
)

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    throw "Python não encontrado no PATH."
}

$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument "manage.py check_status_timeout" `
    -WorkingDirectory $BackendDir

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) -RepetitionDuration ([TimeSpan]::MaxValue)

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "Tarefa '$TaskName' registrada. Intervalo: $IntervalMinutes min."
Write-Host "Comando: python manage.py check_status_timeout"
Write-Host "Diretório: $BackendDir"
