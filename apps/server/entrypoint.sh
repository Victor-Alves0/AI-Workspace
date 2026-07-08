#!/usr/bin/env bash
set -e

echo "==> Aplicando migrações (alembic upgrade head)"
alembic upgrade head

echo "==> Iniciando servidor"
exec uvicorn aiworkspace.main:app --host 0.0.0.0 --port 8000
