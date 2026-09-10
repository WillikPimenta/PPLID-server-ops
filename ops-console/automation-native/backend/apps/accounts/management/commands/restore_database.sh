#!/usr/bin/env bash
# Recria o banco PostgreSQL na máquina destino a partir do snapshot.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT/backend"

if [[ ! -f "data/db_snapshot.json" ]]; then
  echo "Erro: backend/data/db_snapshot.json não encontrado."
  echo "Na máquina de origem execute: python manage.py export_db_snapshot"
  exit 1
fi

echo "==> Restaurando banco PPLID..."
python manage.py restore_db_snapshot "$@"

if [[ -d "../backend/media" ]] || [[ -d "media" ]]; then
  echo "==> Media local detectada."
else
  echo "==> Lembrete: copie backend/media/ da máquina de origem se houver imagens."
fi

echo "==> Concluído."
