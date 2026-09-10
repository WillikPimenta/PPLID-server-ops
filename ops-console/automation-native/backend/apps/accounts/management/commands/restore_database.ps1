# Recria o banco PostgreSQL na máquina destino a partir do snapshot.
$ErrorActionPreference = "Stop"

$Backend = Join-Path $PSScriptRoot ".."
Set-Location $Backend

$snapshot = Join-Path $Backend "data\db_snapshot.json"
if (-not (Test-Path $snapshot)) {
    Write-Error "Arquivo não encontrado: $snapshot`nNa máquina de origem execute: python manage.py export_db_snapshot"
}

Write-Host "==> Restaurando banco PPLID..."
python manage.py restore_db_snapshot @args

if (Test-Path (Join-Path $Backend "media")) {
    Write-Host "==> Pasta media/ presente."
} else {
    Write-Host "==> Lembrete: copie backend/media/ da máquina de origem se houver imagens."
}

Write-Host "==> Concluído."
