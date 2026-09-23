#!/usr/bin/env bash
# Agente de atualização do AI Workspace (servidor Docker, Linux com systemd).
#
# Deixa o botão "Atualizar agora" do painel do admin funcionar SEM dar ao container
# acesso ao Docker: o servidor só grava um pedido em ./data/control (pasta montada do
# host); uma unidade .path do systemd vê o pedido e roda o ./update.sh aqui no host.
#
#   sudo ./scripts/update-agent.sh install     # uma vez
#   sudo ./scripts/update-agent.sh uninstall
#   ./scripts/update-agent.sh run              # o que o systemd executa
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONTROL="${ROOT}/data/control"
UNIT="aiworkspace-update"
# o container roda como o usuário app (uid 10001): precisa gravar o pedido aqui
APP_UID=10001

json_str() {  # texto -> string JSON (sem depender de python/jq no host)
  printf '%s' "$1" | tail -c 4000 | tr -d '\r' \
    | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\t/\\t/g' \
    | awk 'BEGIN { ORS = "\\n"; printf "\"" } { print } END { printf "\"" }'
}

status() {  # status <estado> [log]
  local now; now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '{"state":"%s","at":"%s","log":%s}\n' "$1" "$now" "$(json_str "${2:-}")" \
    > "${CONTROL}/update-status.json.tmp"
  mv -f "${CONTROL}/update-status.json.tmp" "${CONTROL}/update-status.json"
  chmod 0664 "${CONTROL}/update-status.json" 2>/dev/null || true
}

cmd_install() {
  [[ $EUID -eq 0 ]] || { echo "Rode com sudo (instala unidades do systemd)." >&2; exit 1; }
  command -v systemctl >/dev/null || { echo "systemd não encontrado." >&2; exit 1; }
  local owner; owner="$(stat -c %U "$ROOT")"
  mkdir -p "$CONTROL"
  # o dono do checkout roda o update.sh; o container (uid 10001) grava o pedido
  chown "$owner":"$APP_UID" "$CONTROL" 2>/dev/null || chown "$owner" "$CONTROL"
  chmod 0775 "$CONTROL"
  date -u +%Y-%m-%dT%H:%M:%SZ > "${CONTROL}/agent"

  cat > "/etc/systemd/system/${UNIT}.path" <<EOF
[Unit]
Description=AI Workspace: pedido de atualização pelo painel

[Path]
PathExists=${CONTROL}/update-request
Unit=${UNIT}.service

[Install]
WantedBy=multi-user.target
EOF
  cat > "/etc/systemd/system/${UNIT}.service" <<EOF
[Unit]
Description=AI Workspace: atualização (update.sh)

[Service]
Type=oneshot
User=${owner}
WorkingDirectory=${ROOT}
ExecStart=${ROOT}/scripts/update-agent.sh run
EOF
  systemctl daemon-reload
  systemctl enable --now "${UNIT}.path"
  echo "Agente instalado. O botão 'Atualizar agora' do painel já funciona."
  echo "Se o container já estava de pé antes da pasta existir: docker compose up -d"
}

cmd_uninstall() {
  [[ $EUID -eq 0 ]] || { echo "Rode com sudo." >&2; exit 1; }
  systemctl disable --now "${UNIT}.path" 2>/dev/null || true
  rm -f "/etc/systemd/system/${UNIT}.path" "/etc/systemd/system/${UNIT}.service" "${CONTROL}/agent"
  systemctl daemon-reload
  echo "Agente removido."
}

cmd_run() {
  # consome o pedido primeiro: senão o .path dispara de novo em loop
  rm -f "${CONTROL}/update-request"
  status running
  local log
  if log="$(cd "$ROOT" && ./update.sh 2>&1)"; then
    status done "$log"
  else
    status failed "$log"
    exit 1
  fi
}

case "${1:-}" in
  install) cmd_install ;;
  uninstall) cmd_uninstall ;;
  run) cmd_run ;;
  *) echo "uso: $0 install|uninstall|run" >&2; exit 2 ;;
esac
