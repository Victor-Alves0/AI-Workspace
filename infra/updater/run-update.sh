#!/usr/bin/env bash
# Executado pelo updater (container com o socket do Docker). Mesmos passos do
# ./update.sh do host, com três cuidados de quem roda DENTRO de um container:
#  - o git roda como o DONO do checkout (senão os arquivos do projeto viram de root);
#  - atualiza todos os serviços MENOS o próprio updater, grava "done" e só então se
#    recria (recriar-se antes mataria este script no meio);
#  - o projeto está montado no MESMO caminho do host: os binds relativos do compose
#    (./infra/...) são resolvidos pelo daemon com caminhos do host.
set -Eeuo pipefail

STATE="${UPDATER_STATE_DIR:-/run/updater}"
exec > >(tee "$STATE/last.log") 2>&1

status() {  # status <estado>
  python3 - "$1" "$STATE" <<'PY'
import json, os, sys, datetime
state, d = sys.argv[1], sys.argv[2]
log = open(os.path.join(d, "last.log"), encoding="utf-8", errors="replace").read()[-4000:]
tmp = os.path.join(d, "status.tmp")
json.dump({"state": state, "at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "log": log}, open(tmp, "w"))
os.chmod(tmp, 0o644); os.replace(tmp, os.path.join(d, "status.json"))
PY
}

# caminho do projeto NO HOST (rótulo que o compose põe no container) — o projeto está
# montado em /project; um link com o caminho do host faz o compose aqui dentro enxergar
# os mesmos caminhos que o daemon (binds e contextos de build relativos)
DIR="$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$HOSTNAME")"
PROJECT="$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "$HOSTNAME")"
if [[ -z "$DIR" || ! -f /project/docker-compose.yml ]]; then
  echo "Projeto não encontrado (/project) — suba a stack pela pasta do projeto: docker compose up -d"
  exit 1
fi
if [[ "$DIR" != /project && ! -e "$DIR" ]]; then
  mkdir -p "$(dirname "$DIR")"
  ln -s /project "$DIR"
fi
cd "$DIR"
export COMPOSE_PROJECT_NAME="$PROJECT"

uid="$(stat -L -c %u "$DIR")"; gid="$(stat -L -c %g "$DIR")"
git_owner() { setpriv --reuid="$uid" --regid="$gid" --clear-groups env HOME=/tmp git -c safe.directory="$DIR" "$@"; }

echo "==> Verificando alterações locais…"
if [[ -n "$(git_owner status --porcelain)" ]]; then
  echo "Há alterações locais não commitadas no servidor — atualize pelo terminal (./update.sh)."
  exit 1
fi

echo "==> Puxando a versão mais recente…"
git_owner pull --ff-only
export GIT_COMMIT="$(git_owner rev-parse --short=12 HEAD)"

echo "==> Reconstruindo as imagens…"
docker compose build

echo "==> Subindo os serviços (menos o updater)…"
mapfile -t servicos < <(docker compose config --services | grep -vx updater)
# --remove-orphans: serviço que saiu do compose (ex.: um container substituído) é removido
docker compose up -d --remove-orphans "${servicos[@]}"

echo "==> Aplicando migrações do banco…"
docker compose exec -T server alembic upgrade head

echo "==> Concluído: $(docker compose exec -T server python -c 'import aiworkspace; print(aiworkspace.__version__)' || true)"
status done
# por último: recria o próprio updater. NÃO dá para fazer daqui (`up -d updater` para
# este container — matando este processo — antes de iniciar o novo, que fica só
# "Created"). Um container AVULSO (fora do projeto, some sozinho com --rm) faz isso
# depois que este sai de cena.
docker run -d --rm --name "${PROJECT}-updater-selfupdate" \
  -v /var/run/docker.sock:/var/run/docker.sock -v "$DIR:$DIR" -w "$DIR" \
  -e COMPOSE_PROJECT_NAME="$PROJECT" \
  "$(docker inspect -f '{{ .Config.Image }}' "$HOSTNAME")" \
  sh -c 'sleep 3; docker compose up -d --no-deps updater' >/dev/null
