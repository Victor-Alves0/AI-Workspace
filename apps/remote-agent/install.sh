#!/usr/bin/env bash
# Instalador do AI Workspace — Remote Terminal Agent.
#
# Uma passada só: cria o usuário dedicado que roda os comandos, instala o agente e a
# unidade systemd, gera token + certificado TLS e imprime o que você cola no
# AI Workspace (endereço, token, certificado).
#
#   sudo ./install.sh --san 203.0.113.10
#   sudo ./install.sh --san vps.exemplo.com --port 8791 --proxy socks5h://127.0.0.1:9050 --force-egress
#
# O usuário dedicado (`aiw-remote`) não é firula: o killswitch filtra a rede POR UID,
# e é ele que permite selar a saída dos comandos sem tocar na rede do resto da máquina
# (o seu SSH inclusive). Por isso o modo `force` recusa rodar como root.
set -euo pipefail

PREFIX=/opt/aiw-remote-agent
CONFIG_DIR=/etc/aiw-remote-agent
CONFIG="$CONFIG_DIR/config.json"
SERVICE=/etc/systemd/system/aiw-remote-agent.service
RUN_USER=aiw-remote
PORT=8791
BIND=0.0.0.0
SAN=""
PROXY=""
EGRESS_MODE="off"
GRANT_SUDO=0
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<EOF
uso: sudo ./install.sh [opções]

  --san <ip|dominio>   endereço pelo qual o AI Workspace vai chegar (vai no certificado)
  --port <n>           porta do agente (padrão 8791)
  --bind <addr>        interface de escuta (padrão 0.0.0.0; use 127.0.0.1 atrás de túnel)
  --user <nome>        usuário que executa os comandos (padrão aiw-remote)
  --proxy <url>        proxy de saída dos comandos (socks5h://... ou http://...)
  --force-egress       liga o modo 'force': firewall por-uid + killswitch (exige --proxy)
  --env-egress         só variáveis de ambiente (sem killswitch; menos seguro)
  --grant-sudo         dá sudo NOPASSWD ao usuário dos comandos (incompatível com --force-egress)
  --uninstall          remove serviço, regras de firewall e arquivos
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --san) SAN="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --bind) BIND="$2"; shift 2 ;;
    --user) RUN_USER="$2"; shift 2 ;;
    --proxy) PROXY="$2"; shift 2 ;;
    --force-egress) EGRESS_MODE="force"; shift ;;
    --env-egress) EGRESS_MODE="env"; shift ;;
    --grant-sudo) GRANT_SUDO=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "opção desconhecida: $1" >&2; usage; exit 1 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "rode como root (sudo)." >&2; exit 1; }

if [[ "${UNINSTALL:-0}" == "1" ]]; then
  echo "==> removendo o agente"
  systemctl disable --now aiw-remote-agent 2>/dev/null || true
  # as regras de firewall são do agente: sair sem limpá-las deixaria o usuário
  # dedicado sem rede para sempre, e ninguém lembraria de onde veio.
  command -v nft >/dev/null && nft delete table inet aiw_egress 2>/dev/null || true
  if command -v iptables >/dev/null && id -u "$RUN_USER" >/dev/null 2>&1; then
    uid=$(id -u "$RUN_USER")
    iptables -D OUTPUT -m owner --uid-owner "$uid" -j AIW_EGRESS 2>/dev/null || true
    iptables -F AIW_EGRESS 2>/dev/null || true
    iptables -X AIW_EGRESS 2>/dev/null || true
    command -v ip6tables >/dev/null && ip6tables -D OUTPUT -m owner --uid-owner "$uid" -j REJECT 2>/dev/null || true
  fi
  rm -f "$SERVICE" /etc/sudoers.d/aiw-remote
  rm -rf "$PREFIX" "$CONFIG_DIR"
  systemctl daemon-reload
  echo "removido. O usuário '$RUN_USER' foi mantido (apague com: userdel -r $RUN_USER)."
  exit 0
fi

command -v python3 >/dev/null || { echo "python3 é necessário." >&2; exit 1; }
[[ "$EGRESS_MODE" == "off" || -n "$PROXY" ]] || { echo "--force-egress/--env-egress exigem --proxy" >&2; exit 1; }
if [[ "$EGRESS_MODE" == "force" && "$GRANT_SUDO" == "1" ]]; then
  # sudo eleva para uid 0 e o processo ESCAPA da regra que casa pelo uid do usuário
  # dedicado: o killswitch continuaria "ligado" enquanto qualquer `sudo curl` sairia
  # pelo IP real. Preferimos recusar a instalar uma proteção que não protege.
  echo "--grant-sudo anula o killswitch do modo force (sudo vira uid 0 e escapa da regra)." >&2
  exit 1
fi

echo "==> usuário dedicado: $RUN_USER"
id -u "$RUN_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash "$RUN_USER"

echo "==> instalando o agente em $PREFIX"
install -d -m 0755 "$PREFIX"
install -m 0755 "$SRC_DIR/aiw_remote_agent.py" "$PREFIX/aiw_remote_agent.py"
install -d -m 0700 "$CONFIG_DIR"

echo "==> certificado TLS"
if command -v openssl >/dev/null; then
  [[ -n "$SAN" ]] || SAN="$(hostname -I 2>/dev/null | awk '{print $1}')"
  [[ -n "$SAN" ]] || SAN="$(hostname)"
  if [[ "$SAN" =~ ^[0-9.]+$ || "$SAN" == *:* ]]; then ALT="IP:$SAN"; else ALT="DNS:$SAN"; fi
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout "$CONFIG_DIR/agent.key" -out "$CONFIG_DIR/agent.crt" \
    -subj "/CN=aiw-remote-agent" -addext "subjectAltName=$ALT" >/dev/null 2>&1
  chmod 600 "$CONFIG_DIR/agent.key"; chmod 644 "$CONFIG_DIR/agent.crt"
  TLS_CERT="$CONFIG_DIR/agent.crt"; TLS_KEY="$CONFIG_DIR/agent.key"
else
  echo "!! openssl ausente: o agente vai subir SEM TLS. Exponha-o só por um túnel." >&2
  TLS_CERT=""; TLS_KEY=""
fi

echo "==> configuração"
TOKEN="$(python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("="))')"
KILLSWITCH=true
[[ "$EGRESS_MODE" == "off" ]] && KILLSWITCH=false
python3 - "$CONFIG" <<PY
import json, sys
cfg = {
    "token": "$TOKEN",
    "bind": "$BIND",
    "port": int("$PORT"),
    "tls_cert": "$TLS_CERT",
    "tls_key": "$TLS_KEY",
    "run_user": "$RUN_USER",
    "workdir": "",
    "shell": "/bin/bash",
    "max_timeout": 900,
    "allow_cidrs": [],
    "egress": {
        "mode": "$EGRESS_MODE",
        "proxy_url": "$PROXY",
        "dns": "proxy",
        "killswitch": $KILLSWITCH,
        "allow_lan": False,
        "allow_hosts": [],
    },
}
open(sys.argv[1], "w").write(json.dumps(cfg, indent=2))
PY
chmod 600 "$CONFIG"

if [[ "$GRANT_SUDO" == "1" ]]; then
  echo "$RUN_USER ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/aiw-remote
  chmod 440 /etc/sudoers.d/aiw-remote
  echo "==> sudo NOPASSWD concedido a $RUN_USER"
fi

echo "==> serviço systemd"
cat > "$SERVICE" <<EOF
[Unit]
Description=AI Workspace Remote Terminal Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
# Roda como root de propósito: é o que permite trocar para o usuário dedicado a cada
# comando e instalar as regras de firewall do killswitch. Os COMANDOS nunca rodam
# como root — o agente rebaixa o privilégio antes de executar.
User=root
ExecStart=/usr/bin/env python3 $PREFIX/aiw_remote_agent.py --config $CONFIG
Restart=always
RestartSec=3
NoNewPrivileges=no

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now aiw-remote-agent
sleep 1
systemctl is-active --quiet aiw-remote-agent || { journalctl -u aiw-remote-agent -n 30 --no-pager; exit 1; }

SCHEME=https; [[ -z "$TLS_CERT" ]] && SCHEME=http
echo
echo "================= COLE ISTO NO AI WORKSPACE ================="
echo "Configurações → Integrações → Remote Terminal → Adicionar máquina"
echo
echo "Endereço: $SCHEME://$SAN:$PORT"
echo "Token:    $TOKEN"
if [[ -n "$TLS_CERT" ]]; then
  echo
  echo "Certificado (modo de TLS 'pinned'):"
  cat "$TLS_CERT"
fi
echo "============================================================"
echo
echo "Política de saída: $EGRESS_MODE${PROXY:+ via $PROXY}"
"$PREFIX/aiw_remote_agent.py" --config "$CONFIG" --status 2>/dev/null || \
  python3 "$PREFIX/aiw_remote_agent.py" --config "$CONFIG" --status
echo
echo "Abra a porta $PORT no firewall/security group se o agente for alcançado pela internet."
