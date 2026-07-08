#!/usr/bin/env bash
# Atualiza o AI Workspace no host (abordagem segura: SEM Docker socket no
# container). Rode este script na máquina que hospeda o app quando o painel do
# admin indicar que há uma atualização disponível.
#
#   ./update.sh
#
# Ele: puxa o código novo do git, reconstrói as imagens, sobe os containers e
# aplica as migrações do banco.
set -euo pipefail

cd "$(dirname "$0")"

echo "==> Verificando alterações locais…"
if [[ -n "$(git status --porcelain)" ]]; then
  echo "AVISO: há alterações locais não commitadas. Faça stash/commit antes de atualizar." >&2
  exit 1
fi

echo "==> Puxando a versão mais recente…"
git pull --ff-only

echo "==> Reconstruindo as imagens…"
docker compose build

echo "==> Subindo os containers…"
docker compose up -d

echo "==> Aplicando migrações do banco…"
docker compose exec -T server alembic upgrade head

echo "==> Concluído. Versão atual:"
docker compose exec -T server python -c "import aiworkspace; print(aiworkspace.__version__)" || true
