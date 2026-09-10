# Agenda o reset diário de status às 05:30 (horário local do servidor).
# Uso (PowerShell como administrador):
#   .\backend\scripts\reset_daily_status_scheduled.ps1 -BackendDir "C:\Users\c91763a\PPLID\backend"

param(
    [string]$BackendDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$TaskName = "PPLID Reset Daily Status",
    [string]$RunAt = "05:30"
)

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    throw "Python não encontrado no PATH."
}

$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument "manage.py reset_daily_status" `
    -WorkingDirectory $BackendDir

$trigger = New-ScheduledTaskTrigger -Daily -At $RunAt

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "Tarefa '$TaskName' registrada para $RunAt diariamente."
Write-Host "Comando: python manage.py reset_daily_status"
Write-Host "Diretório: $BackendDir"
