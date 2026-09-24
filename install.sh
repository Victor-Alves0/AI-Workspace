#!/usr/bin/env bash
# Instalação rápida do AI Workspace para Linux/macOS.
# Pré-requisitos deliberadamente pequenos: Git, Docker e Docker Compose v2.
set -Eeuo pipefail

readonly REPO_URL="https://github.com/Victor-Alves0/AI-Workspace.git"
readonly DEFAULT_DIR="${PWD}/AI-Workspace"
INSTALL_DIR="${AI_WORKSPACE_DIR:-$DEFAULT_DIR}"

fail() {
  printf 'Erro: %s\n' "$*" >&2
  exit 1
}

need() {
  command -v "$1" >/dev/null 2>&1 || fail "$2"
}

random_hex() {
  # `od` faz parte do coreutils/BSD base e evita exigir Python ou OpenSSL.
  od -An -N48 -tx1 /dev/urandom | tr -d ' \n'
}

set_env_value() {
  local key="$1" value="$2" file="$3"
  sed -i.bak "s|^${key}=.*$|${key}=${value}|" "$file"
  rm -f "${file}.bak"
}

need git "instale o Git e execute novamente."
need docker "instale o Docker Engine/Desktop e execute novamente."
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 não foi encontrado (`docker compose`)."
docker info >/dev/null 2>&1 || fail "o Docker não está em execução ou seu usuário não tem acesso a ele."

# Quando executado como `./install.sh` dentro do repositório, usa o checkout atual.
# Quando vem de `curl | bash`, BASH_SOURCE aponta para um pipe e clonamos no destino.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-}")" 2>/dev/null && pwd || true)"
if [[ -f "${SCRIPT_DIR}/docker-compose.yml" && -d "${SCRIPT_DIR}/.git" ]]; then
  INSTALL_DIR="$SCRIPT_DIR"
elif [[ -d "${INSTALL_DIR}/.git" ]]; then
  printf '==> Atualizando checkout existente em %s\n' "$INSTALL_DIR"
  git -C "$INSTALL_DIR" pull --ff-only origin main
elif [[ -e "$INSTALL_DIR" ]]; then
  fail "o destino já existe e não é um checkout Git: ${INSTALL_DIR}"
else
  printf '==> Clonando AI Workspace em %s\n' "$INSTALL_DIR"
  git clone --depth 1 --branch main "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

if [[ ! -f .env ]]; then
  printf '==> Criando configuração e segredos locais\n'
  cp .env.example .env
  set_env_value APP_SECRET "$(random_hex)" .env
  set_env_value POSTGRES_PASSWORD "$(random_hex)" .env
  chmod 600 .env 2>/dev/null || true
else
  printf '==> Preservando o .env existente\n'
fi

printf '==> Construindo e iniciando os serviços principais\n'
export GIT_COMMIT="$(git rev-parse --short=12 HEAD)"
docker compose up -d --build

WEB_PORT="$(sed -n 's/^WEB_PORT=//p' .env | tail -n 1)"
WEB_PORT="${WEB_PORT:-3000}"
printf '\nAI Workspace instalado. Abra: http://localhost:%s\n' "$WEB_PORT"
printf 'Diretório: %s\n' "$INSTALL_DIR"
printf 'Atualizações futuras: cd %q && ./update.sh\n' "$INSTALL_DIR"
