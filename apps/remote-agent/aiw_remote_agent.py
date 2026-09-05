#!/usr/bin/env python3
"""AI Workspace — Remote Terminal Agent.

Roda NA SUA MÁQUINA (VPS, servidor de casa, workstation) e dá ao AI Workspace um
terminal ali dentro. Um arquivo, só biblioteca padrão: numa VPS crua você copia,
roda o instalador e acabou — sem pip, sem venv, sem imagem de container.

Duas coisas ele faz, e a segunda é a razão de existir em vez de "só use SSH":

  1. **Terminal.** API HTTPS mínima (`exec`, `jobs`) autenticada por token, em vez de
     um shell interativo completo. A superfície é pequena, o token é revogável e cada
     comando é um evento auditável.

  2. **Saída controlada, com killswitch.** Todo comando sai pelo proxy que você
     escolher, com o DNS resolvido lá — e uma regra de firewall por-uid DESCARTA
     qualquer outra saída. Se o proxy cair, o comando FALHA em vez de vazar pela rota
     normal. É a diferença entre "configurei o proxy" e "não existe caminho sem ele":
     um `curl` que ignora as variáveis de ambiente, um binário com resolvedor próprio
     ou um `ping` bastam para expor o IP real quando a única defesa é o env.

Por que as regras casam por UID: os comandos rodam como um usuário dedicado
(`aiw-remote`), e o firewall filtra exatamente aquele uid. Assim o resto da máquina —
seus serviços, seu SSH — continua com a rede intacta. É também por isso que o modo
`force` recusa rodar como root: filtrar o uid 0 derrubaria a rede da máquina inteira.

Uso:
    aiw_remote_agent.py --config /etc/aiw-remote-agent/config.json
    aiw_remote_agent.py --print-token       # mostra o token da conexão
    aiw_remote_agent.py --status            # estado da política de saída

Licença: mesma do AI Workspace.
"""

from __future__ import annotations

import argparse
import base64
import hmac
import json
import logging
import os
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

# O agente RODA em POSIX (troca de usuário e firewall são a razão de existir), mas
# precisa IMPORTAR em qualquer lugar: a lógica do killswitch — política, regras e a
# decisão de recusar — é a parte crítica, e ela é testada no CI, que nem sempre é Linux.
try:
    import pwd
except ImportError:  # pragma: no cover - Windows: só o caminho de import/teste
    pwd = None  # type: ignore[assignment]


def geteuid() -> int:
    """uid efetivo; -1 onde o SO não tem o conceito (nunca é 0, logo "não é root")."""
    return os.geteuid() if hasattr(os, "geteuid") else -1


AGENT_VERSION = "1.0.0"
PROTOCOL = 1

DEFAULT_CONFIG_PATH = "/etc/aiw-remote-agent/config.json"
DEFAULT_PORT = 8791
# Teto de saída por comando. A CAUDA é o que se guarda: o fim de um build/log é onde
# está o erro; o começo costuma ser banner e barra de progresso.
OUTPUT_CAP = 200_000
JOB_RETENTION = 3600  # segundos que um job concluído fica consultável

log = logging.getLogger("aiw-agent")

# Redes privadas liberadas quando `allow_lan` está ligado (acesso à rede interna da
# VPS sem passar pelo proxy — útil para bancos internos, arriscado para anonimato).
LAN_CIDRS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
             "100.64.0.0/10")

NFT_TABLE = "aiw_egress"


# --------------------------------------------------------------------------- #
# Configuração
# --------------------------------------------------------------------------- #
DEFAULT_EGRESS: Dict[str, Any] = {
    "mode": "off",          # off | env | force
    "proxy_url": "",        # socks5h://user:pass@host:port | http://host:port
    "dns": "proxy",         # proxy = sem DNS local; system = resolvedor da máquina
    "killswitch": True,     # política impossível de aplicar => comando recusado
    "allow_lan": False,
    "allow_hosts": [],
}


def default_config() -> Dict[str, Any]:
    return {
        "token": "",
        "bind": "0.0.0.0",
        "port": DEFAULT_PORT,
        "tls_cert": "",
        "tls_key": "",
        # usuário que EXECUTA os comandos. O modo `force` exige que não seja root.
        "run_user": "aiw-remote",
        "workdir": "",
        "shell": "/bin/bash",
        "max_timeout": 900,
        # allowlist de quem pode falar com o agente (vazio = qualquer origem; o token
        # continua obrigatório). Útil quando só o túnel deve alcançar a porta.
        "allow_cidrs": [],
        "egress": dict(DEFAULT_EGRESS),
    }


def load_config(path: str) -> Dict[str, Any]:
    cfg = default_config()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg.update(json.load(fh) or {})
    except FileNotFoundError:
        pass
    except (ValueError, OSError) as exc:
        raise SystemExit("config ilegível em %s: %s" % (path, exc))
    eg = dict(DEFAULT_EGRESS)
    eg.update(cfg.get("egress") or {})
    cfg["egress"] = eg
    return cfg


def save_config(path: str, cfg: Dict[str, Any]) -> None:
    """Grava com 0600 e troca atômica: a config carrega o token e a senha do proxy, e
    um leitor concorrente nunca deve ver o arquivo pela metade."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".config.%s.tmp" % uuid.uuid4().hex[:8])
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def mask(url: str) -> str:
    return re.sub(r"://([^:/@]+):([^@]+)@", r"://\1:***@", url or "")


# --------------------------------------------------------------------------- #
# Política de saída — env
# --------------------------------------------------------------------------- #
def proxy_parts(url: str) -> Tuple[str, str, int, str, str]:
    """(esquema, host, porta, usuário, senha). Esquema normalizado: socks5 | http."""
    p = urlparse((url or "").strip())
    scheme = (p.scheme or "").lower()
    if scheme in ("socks5h", "socks5"):
        scheme = "socks5"
    elif scheme in ("http", "https"):
        scheme = "http"
    else:
        raise ValueError("esquema de proxy não suportado: %r" % (p.scheme or ""))
    if not p.hostname:
        raise ValueError("URL de proxy sem host")
    port = p.port or (1080 if scheme == "socks5" else 8080)
    return scheme, p.hostname, int(port), p.username or "", p.password or ""


def proxy_env(policy: Dict[str, Any]) -> Dict[str, str]:
    """Variáveis de proxy para o comando.

    SOCKS sai como `socks5h://` de propósito: o "h" manda o cliente entregar o NOME ao
    proxy em vez de resolver antes. Sem ele, curl/git resolvem localmente e o DNS
    vaza mesmo com o tráfego tunelado — o vazamento mais fácil de não perceber.
    """
    url = (policy.get("proxy_url") or "").strip()
    if not url:
        return {}
    scheme, host, port, user, pwd_ = proxy_parts(url)
    auth = ("%s:%s@" % (user, pwd_)) if user else ""
    if scheme == "socks5":
        full = "socks5h://%s%s:%d" % (auth, host, port)
    else:
        full = "http://%s%s:%d" % (auth, host, port)
    env = {
        "http_proxy": full, "https_proxy": full, "all_proxy": full, "ftp_proxy": full,
        "HTTP_PROXY": full, "HTTPS_PROXY": full, "ALL_PROXY": full, "FTP_PROXY": full,
        "no_proxy": "localhost,127.0.0.1,::1",
        "NO_PROXY": "localhost,127.0.0.1,::1",
        # git respeita http.proxy; curl lê o .curlrc, não mexemos nele.
        "GIT_HTTP_PROXY": full,
    }
    return env


# --------------------------------------------------------------------------- #
# Política de saída — firewall (o killswitch de verdade)
# --------------------------------------------------------------------------- #
def _run(cmd, stdin_text: Optional[str] = None, timeout: int = 20):
    return subprocess.run(cmd, input=stdin_text, capture_output=True, text=True,
                          timeout=timeout)


def have(binary: str) -> bool:
    return shutil.which(binary) is not None


def resolve_uid(user: str) -> Optional[int]:
    if pwd is None:
        return None
    try:
        return pwd.getpwnam(user).pw_uid
    except KeyError:
        return None


def _nft_ruleset(uid: int, policy: Dict[str, Any]) -> str:
    """Ruleset nftables da tabela do agente.

    Política do CHAIN é `accept` e a última regra do uid é `reject`: assim tudo que não
    é do uid vigiado passa intacto (o resto da máquina não sente a regra), e tudo que é
    do uid e não casou com uma liberação morre ali. `reject` em vez de `drop` porque o
    comando falha na hora com "connection refused" em vez de pendurar até o timeout —
    o agente precisa devolver um erro compreensível, não um travamento.
    """
    scheme, host, port, _u, _p = proxy_parts(policy["proxy_url"])
    lines = [
        "table inet %s {" % NFT_TABLE,
        "  chain output {",
        "    type filter hook output priority 0; policy accept;",
        "    meta skuid != %d accept" % uid,
        "    oifname \"lo\" accept",
        "    ip daddr 127.0.0.0/8 accept",
        "    ip6 daddr ::1 accept",
    ]
    # o proxy em si precisa ser alcançável — é a única porta de saída legítima
    if _is_ip(host):
        fam = "ip6" if ":" in host else "ip"
        lines.append("    %s daddr %s tcp dport %d accept" % (fam, host, port))
    else:
        # proxy por NOME exigiria DNS para ser alcançado, e é justamente o DNS que
        # estamos cortando. Resolvemos AQUI, uma vez, e fixamos os IPs na regra.
        for ip in _resolve_all(host):
            fam = "ip6" if ":" in ip else "ip"
            lines.append("    %s daddr %s tcp dport %d accept" % (fam, ip, port))
    if policy.get("dns") == "system":
        lines.append("    udp dport 53 accept")
        lines.append("    tcp dport 53 accept")
    if policy.get("allow_lan"):
        lines.append("    ip daddr { %s } accept" % ", ".join(LAN_CIDRS))
    for extra in policy.get("allow_hosts") or []:
        if _is_ip(extra):
            fam = "ip6" if ":" in extra else "ip"
            lines.append("    %s daddr %s accept" % (fam, extra))
    lines += ["    reject", "  }", "}"]
    return "\n".join(lines) + "\n"


def _is_ip(value: str) -> bool:
    for fam in (socket.AF_INET, socket.AF_INET6):
        try:
            socket.inet_pton(fam, value)
            return True
        except (OSError, ValueError):
            continue
    return False


def _resolve_all(host: str):
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return []
    return sorted({i[4][0] for i in infos})


def apply_firewall(uid: int, policy: Dict[str, Any]) -> Tuple[bool, str]:
    """Instala as regras. (ok, detalhe). Nunca levanta — o chamador decide o que
    fazer com a falha (com killswitch ligado, ela vira recusa de executar)."""
    if geteuid() != 0:
        return False, "o agente não está rodando como root — sem privilégio para o firewall"
    if have("nft"):
        try:
            _run(["nft", "delete", "table", "inet", NFT_TABLE])  # idempotência
            r = _run(["nft", "-f", "-"], stdin_text=_nft_ruleset(uid, policy))
            if r.returncode == 0:
                return True, "nftables"
            return False, "nft falhou: %s" % (r.stderr or r.stdout or "")[:200]
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            return False, "nft indisponível: %s" % exc
    if have("iptables"):
        ok, detail = _apply_iptables(uid, policy)
        return ok, detail
    return False, "nem nft nem iptables encontrados nesta máquina"


def _apply_iptables(uid: int, policy: Dict[str, Any]) -> Tuple[bool, str]:
    """Mesma política em iptables (kernels/distros sem nft). Cadeia própria para poder
    limpar sem tocar nas regras de quem já usava o firewall."""
    chain = "AIW_EGRESS"
    try:
        scheme, host, port, _u, _p = proxy_parts(policy["proxy_url"])
    except ValueError as exc:
        return False, str(exc)
    cmds = [
        ["iptables", "-D", "OUTPUT", "-m", "owner", "--uid-owner", str(uid), "-j", chain],
        ["iptables", "-F", chain],
        ["iptables", "-X", chain],
        ["iptables", "-N", chain],
        ["iptables", "-A", chain, "-o", "lo", "-j", "RETURN"],
        ["iptables", "-A", chain, "-d", "127.0.0.0/8", "-j", "RETURN"],
    ]
    targets = [host] if _is_ip(host) else _resolve_all(host)
    for ip in targets:
        if ":" in ip:
            continue  # cadeia IPv4; o IPv6 é fechado abaixo
        cmds.append(["iptables", "-A", chain, "-d", ip, "-p", "tcp", "--dport",
                     str(port), "-j", "RETURN"])
    if policy.get("dns") == "system":
        cmds.append(["iptables", "-A", chain, "-p", "udp", "--dport", "53", "-j", "RETURN"])
        cmds.append(["iptables", "-A", chain, "-p", "tcp", "--dport", "53", "-j", "RETURN"])
    if policy.get("allow_lan"):
        for cidr in LAN_CIDRS:
            cmds.append(["iptables", "-A", chain, "-d", cidr, "-j", "RETURN"])
    for extra in policy.get("allow_hosts") or []:
        if _is_ip(extra) and ":" not in extra:
            cmds.append(["iptables", "-A", chain, "-d", extra, "-j", "RETURN"])
    cmds.append(["iptables", "-A", chain, "-j", "REJECT"])
    cmds.append(["iptables", "-I", "OUTPUT", "-m", "owner", "--uid-owner", str(uid),
                 "-j", chain])
    failed = ""
    for i, cmd in enumerate(cmds):
        r = _run(cmd)
        # as 3 primeiras são limpeza: falham na primeira execução, e isso é esperado
        if r.returncode != 0 and i >= 3:
            failed = "%s -> %s" % (" ".join(cmd), (r.stderr or "").strip()[:120])
            break
    if failed:
        return False, "iptables falhou: %s" % failed
    # IPv6: sem rota permitida, fecha tudo para o uid (o proxy IPv6 não é suportado
    # neste caminho de compatibilidade — melhor bloquear que deixar uma via aberta).
    if have("ip6tables"):
        _run(["ip6tables", "-D", "OUTPUT", "-m", "owner", "--uid-owner", str(uid),
              "-j", "REJECT"])
        _run(["ip6tables", "-I", "OUTPUT", "-m", "owner", "--uid-owner", str(uid),
              "-j", "REJECT"])
    return True, "iptables"


def clear_firewall(uid: Optional[int]) -> None:
    """Remove as regras do agente (modo off, ou parada limpa do serviço)."""
    if geteuid() != 0:
        return
    if have("nft"):
        _run(["nft", "delete", "table", "inet", NFT_TABLE])
    if have("iptables") and uid is not None:
        _run(["iptables", "-D", "OUTPUT", "-m", "owner", "--uid-owner", str(uid),
              "-j", "AIW_EGRESS"])
        _run(["iptables", "-F", "AIW_EGRESS"])
        _run(["iptables", "-X", "AIW_EGRESS"])
    if have("ip6tables") and uid is not None:
        _run(["ip6tables", "-D", "OUTPUT", "-m", "owner", "--uid-owner", str(uid),
              "-j", "REJECT"])


def proxy_reachable(policy: Dict[str, Any], timeout: float = 4.0) -> Tuple[bool, str]:
    """O proxy aceita conexão AGORA? É a checagem que transforma "configurado" em
    "funcionando": com killswitch ligado, um proxy fora do ar precisa virar recusa
    imediata e explicada, não um comando que trava até o timeout."""
    try:
        _s, host, port, _u, _p = proxy_parts(policy.get("proxy_url") or "")
    except ValueError as exc:
        return False, str(exc)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, ""
    except OSError as exc:
        return False, "proxy %s:%d inacessível (%s)" % (host, port, exc)


# --------------------------------------------------------------------------- #
# Execução
# --------------------------------------------------------------------------- #
class Executor:
    """Roda comandos aplicando a política. Uma instância por agente."""

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.fw_state = {"applied": False, "backend": "", "detail": ""}

    # -- política ---------------------------------------------------------- #
    @property
    def policy(self) -> Dict[str, Any]:
        return self.cfg["egress"]

    def run_uid(self) -> Optional[int]:
        user = (self.cfg.get("run_user") or "").strip()
        if not user or geteuid() != 0:
            return None  # sem root não há troca de usuário: roda como o próprio agente
        return resolve_uid(user)

    def apply_policy(self) -> Dict[str, Any]:
        """(Re)aplica a política atual e devolve o estado observável.

        `degraded` é um resultado de primeira classe: significa que a intenção era
        `force` mas o firewall não subiu. Com killswitch, o agente passa a RECUSAR
        comandos — falhar alto é o comportamento correto quando a garantia sumiu; o
        contrário seria continuar rodando sem proteção e sem ninguém saber.
        """
        pol = self.policy
        mode = pol.get("mode") or "off"
        uid = self.run_uid()
        if mode == "off":
            clear_firewall(uid)
            self.fw_state = {"applied": False, "backend": "", "detail": ""}
            return self.state()
        if not (pol.get("proxy_url") or "").strip():
            self.fw_state = {"applied": False, "backend": "",
                             "detail": "modo '%s' sem proxy configurado" % mode}
            return self.state()
        if mode == "env":
            clear_firewall(uid)
            self.fw_state = {"applied": False, "backend": "env",
                             "detail": "somente variáveis de ambiente — sem killswitch"}
            return self.state()
        # force
        if uid is None or uid == 0:
            clear_firewall(uid)
            self.fw_state = {
                "applied": False, "backend": "",
                "detail": ("o modo 'force' exige rodar os comandos como um usuário "
                           "dedicado não-root (run_user). Filtrar o uid 0 derrubaria a "
                           "rede da máquina inteira, então o agente não faz isso."),
            }
            return self.state()
        ok, detail = apply_firewall(uid, pol)
        self.fw_state = {"applied": ok, "backend": detail if ok else "", "detail": detail}
        return self.state()

    def state(self) -> Dict[str, Any]:
        pol = self.policy
        uid = self.run_uid()
        mode = pol.get("mode") or "off"
        enforced = bool(self.fw_state.get("applied"))
        return {
            "mode": mode,
            "proxy": mask(pol.get("proxy_url") or ""),
            "dns": pol.get("dns") or "proxy",
            "killswitch": pol.get("killswitch", True) is not False,
            "allow_lan": bool(pol.get("allow_lan")),
            "allow_hosts": list(pol.get("allow_hosts") or []),
            "run_user": self.cfg.get("run_user") or "",
            "run_uid": uid,
            "enforced": enforced,
            "backend": self.fw_state.get("backend") or "",
            "detail": self.fw_state.get("detail") or "",
            # o que o usuário mais precisa saber, numa palavra
            "status": ("enforced" if (mode == "force" and enforced)
                       else "degraded" if mode == "force"
                       else "env-only" if mode == "env" else "open"),
        }

    def guard(self) -> Optional[str]:
        """Motivo para RECUSAR executar agora, ou None. É o killswitch em ação."""
        pol = self.policy
        mode = pol.get("mode") or "off"
        if mode == "off" or pol.get("killswitch", True) is False:
            return None
        if not (pol.get("proxy_url") or "").strip():
            return ("killswitch: a política de saída exige proxy e nenhum está "
                    "configurado — nenhum comando roda até definir o proxy ou "
                    "desligar o killswitch.")
        if mode == "force" and not self.fw_state.get("applied"):
            return ("killswitch: as regras de firewall NÃO estão ativas (%s). Sem elas "
                    "o comando poderia sair pelo IP real, então o agente recusa "
                    "executar." % (self.fw_state.get("detail") or "motivo desconhecido"))
        ok, why = proxy_reachable(pol)
        if not ok:
            return "killswitch: %s — nenhum comando roda enquanto a saída não estiver " \
                   "garantida." % why
        return None

    # -- subprocesso -------------------------------------------------------- #
    def _preexec(self, uid: Optional[int]):
        def _apply():  # pragma: no cover - roda no filho
            os.setsid()  # grupo próprio: dá para matar a árvore inteira
            if uid is not None and pwd is not None:
                try:
                    rec = pwd.getpwuid(uid)
                    os.setgid(rec.pw_gid)
                    os.initgroups(rec.pw_name, rec.pw_gid)
                except Exception:
                    pass
                os.setuid(uid)
        return _apply

    def _env(self, extra: Optional[Dict[str, str]], uid: Optional[int]) -> Dict[str, str]:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("AIW_AGENT", "AIW_TOKEN"))}
        env.pop("AIW_CONFIG", None)
        if uid is not None and pwd is not None:
            try:
                rec = pwd.getpwuid(uid)
                env["HOME"], env["USER"], env["LOGNAME"] = rec.pw_dir, rec.pw_name, rec.pw_name
            except KeyError:
                pass
        if (self.policy.get("mode") or "off") != "off":
            env.update(proxy_env(self.policy))
        env["AIW_REMOTE_AGENT"] = AGENT_VERSION
        if extra:
            env.update({str(k): str(v) for k, v in extra.items()})
        return env

    def spawn(self, command: str, cwd: str = "", env: Optional[Dict[str, str]] = None,
              shell: str = "") -> subprocess.Popen:
        uid = self.run_uid()
        workdir = cwd or self.cfg.get("workdir") or ""
        if not workdir or not os.path.isdir(workdir):
            workdir = _home_of(uid) or "/tmp"
        sh = shell or self.cfg.get("shell") or "/bin/sh"
        if not os.path.exists(sh):
            sh = "/bin/sh"
        return subprocess.Popen(
            [sh, "-lc", command], cwd=workdir,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", env=self._env(env, uid),
            preexec_fn=self._preexec(uid),
        )

    def run(self, command: str, cwd: str = "", timeout: int = 120,
            env: Optional[Dict[str, str]] = None, shell: str = "") -> Dict[str, Any]:
        blocked = self.guard()
        if blocked:
            return {"error": blocked, "blocked": True, "egress": self.state()}
        timeout = max(1, min(int(timeout or 120), int(self.cfg.get("max_timeout") or 900)))
        started = time.time()
        try:
            proc = self.spawn(command, cwd, env, shell)
        except (OSError, ValueError) as exc:
            return {"error": "não consegui iniciar o comando: %s" % exc}
        timed_out = False
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_tree(proc)
            try:
                out, _ = proc.communicate(timeout=10)
            except Exception:  # noqa: BLE001
                out = ""
        out, truncated = cap(out)
        return {
            "exit_code": None if timed_out else proc.returncode,
            "output": out,
            "seconds": round(time.time() - started, 2),
            "truncated": truncated,
            "timed_out": timed_out,
            "cwd": cwd or self.cfg.get("workdir") or "",
        }

    # -- jobs em background -------------------------------------------------- #
    def start_job(self, command: str, cwd: str = "", env: Optional[Dict[str, str]] = None,
                  shell: str = "") -> Dict[str, Any]:
        blocked = self.guard()
        if blocked:
            return {"error": blocked, "blocked": True, "egress": self.state()}
        try:
            proc = self.spawn(command, cwd, env, shell)
        except (OSError, ValueError) as exc:
            return {"error": "não consegui iniciar o comando: %s" % exc}
        job_id = uuid.uuid4().hex[:12]
        job = {
            "id": job_id, "command": command, "status": "running", "exit_code": None,
            "output": "", "started": time.time(), "seconds": 0.0, "truncated": False,
            "proc": proc, "done": threading.Event(),
        }
        with self.lock:
            self.jobs[job_id] = job
            self._reap_locked()
        threading.Thread(target=self._pump, args=(job,), daemon=True).start()
        return {"job_id": job_id, "status": "running", "command": command[:400]}

    def _pump(self, job: Dict[str, Any]) -> None:
        proc = job["proc"]
        chunks = []
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                chunks.append(line)
                if len(chunks) > 20000:
                    del chunks[:10000]  # mantém a cauda sem crescer sem limite
        except Exception:  # noqa: BLE001
            pass
        proc.wait()
        out, truncated = cap("".join(chunks))
        with self.lock:
            job["output"], job["truncated"] = out, truncated
            job["exit_code"] = proc.returncode
            job["seconds"] = round(time.time() - job["started"], 2)
            job["status"] = ("killed" if proc.returncode is not None and proc.returncode < 0
                             else "done" if proc.returncode == 0 else "failed")
            job["finished"] = time.time()
        job["done"].set()

    def job_view(self, job: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in job.items() if k not in ("proc", "done")}

    def get_job(self, job_id: str, wait: int = 0) -> Dict[str, Any]:
        with self.lock:
            job = self.jobs.get(job_id)
        if job is None:
            return {"error": "job '%s' não existe (ou já expirou)" % job_id}
        if wait > 0:
            job["done"].wait(timeout=min(int(wait), 600))
        with self.lock:
            return self.job_view(job)

    def kill(self, job_id: str) -> Dict[str, Any]:
        with self.lock:
            job = self.jobs.get(job_id)
        if job is None:
            return {"error": "job '%s' não existe" % job_id}
        kill_tree(job["proc"])
        return {"ok": True, "job_id": job_id}

    def list_jobs(self) -> Dict[str, Any]:
        with self.lock:
            self._reap_locked()
            return {"jobs": [
                {k: v for k, v in self.job_view(j).items() if k != "output"}
                for j in self.jobs.values()
            ]}

    def _reap_locked(self) -> None:
        now = time.time()
        for jid in [j for j, v in self.jobs.items()
                    if v.get("finished") and now - v["finished"] > JOB_RETENTION]:
            self.jobs.pop(jid, None)


def _home_of(uid: Optional[int]) -> str:
    if uid is None or pwd is None:
        return os.path.expanduser("~")
    try:
        return pwd.getpwuid(uid).pw_dir
    except KeyError:
        return ""


def cap(out: Optional[str]) -> Tuple[str, bool]:
    out = out or ""
    raw = out.encode("utf-8", "ignore")
    if len(raw) <= OUTPUT_CAP:
        return out, False
    return raw[-OUTPUT_CAP:].decode("utf-8", "ignore"), True


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# Teste de vazamento
# --------------------------------------------------------------------------- #
# Roda COMO O USUÁRIO DOS COMANDOS, pelo mesmo caminho de execução — é a única forma
# de o teste medir a política real (uid, env e firewall) em vez de medir o agente, que
# é root e não está sujeito às regras. Vai como fonte embutida porque precisa ser
# autossuficiente: em `force` com dns=proxy a máquina não resolve nomes nem instala nada.
LEAK_TEST_SRC = r'''
import json, os, socket, sys
from urllib.parse import urlparse

def socks5_get(proxy, host, path):
    p = urlparse(proxy)
    s = socket.create_connection((p.hostname, p.port or 1080), timeout=12)
    user, pwd = p.username or "", p.password or ""
    methods = b"\x02" if user else b"\x00"
    s.sendall(b"\x05\x01" + methods)
    if s.recv(2)[1:2] == b"\x02":
        s.sendall(b"\x01" + bytes([len(user)]) + user.encode() + bytes([len(pwd)]) + pwd.encode())
        if s.recv(2)[1:2] != b"\x00":
            raise RuntimeError("proxy recusou as credenciais")
    hb = host.encode()
    s.sendall(b"\x05\x01\x00\x03" + bytes([len(hb)]) + hb + (80).to_bytes(2, "big"))
    rep = s.recv(4)
    if len(rep) < 2 or rep[1] != 0:
        raise RuntimeError("proxy recusou o CONNECT (%r)" % rep)
    atyp = rep[3] if len(rep) > 3 else 1
    s.recv({1: 4, 4: 16}.get(atyp, 1 + s.recv(1)[0])); s.recv(2)
    s.sendall(("GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n" % (path, host)).encode())
    buf = b""
    while len(buf) < 4096:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    s.close()
    return buf.split(b"\r\n\r\n", 1)[-1].decode("utf-8", "replace").strip()

def http_get(proxy, host, path):
    p = urlparse(proxy)
    s = socket.create_connection((p.hostname, p.port or 8080), timeout=12)
    s.sendall(("GET http://%s%s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n"
               % (host, path, host)).encode())
    buf = b""
    while len(buf) < 4096:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    s.close()
    return buf.split(b"\r\n\r\n", 1)[-1].decode("utf-8", "replace").strip()

out = {}
proxy = os.environ.get("all_proxy") or os.environ.get("ALL_PROXY") or ""
out["proxy_env"] = bool(proxy)
try:
    if proxy.startswith("socks"):
        out["proxy_ip"] = socks5_get(proxy, "api.ipify.org", "/")[:64]
    elif proxy:
        out["proxy_ip"] = http_get(proxy, "api.ipify.org", "/")[:64]
    else:
        out["proxy_ip"] = ""
        out["proxy_error"] = "sem proxy no ambiente"
except Exception as exc:
    out["proxy_ip"] = ""
    out["proxy_error"] = str(exc)[:200]

# O resultado que importa: uma conexão CRUA para fora tem que FALHAR.
try:
    socket.create_connection(("1.1.1.1", 443), timeout=6).close()
    out["direct_blocked"] = False
except OSError as exc:
    out["direct_blocked"] = True
    out["direct_error"] = str(exc)[:120]

try:
    socket.getaddrinfo("example.com", 80)
    out["dns_blocked"] = False
except Exception as exc:
    out["dns_blocked"] = True
    out["dns_error"] = str(exc)[:120]

print(json.dumps(out))
'''


def leak_test(ex: Executor) -> Dict[str, Any]:
    """Prova (ou desmente) a política, medindo pelo lado de dentro."""
    src = LEAK_TEST_SRC.replace("'", "'\\''")
    cmd = "%s -c '%s'" % (sys.executable or "python3", src)
    # Ignora o killswitch de propósito: o teste existe justamente para diagnosticar por
    # que ele está mordendo. É um comando fechado, escrito aqui, sem entrada do usuário.
    saved = ex.policy.get("killswitch", True)
    ex.policy["killswitch"] = False
    try:
        r = ex.run(cmd, timeout=60)
    finally:
        ex.policy["killswitch"] = saved
    if r.get("error"):
        return {"ok": False, "error": r["error"], "egress": ex.state()}
    raw = (r.get("output") or "").strip().splitlines()
    data: Dict[str, Any] = {}
    for line in reversed(raw):
        try:
            data = json.loads(line)
            break
        except ValueError:
            continue
    if not data:
        return {"ok": False, "error": "teste não produziu resultado: %s" % ("\n".join(raw))[-400:],
                "egress": ex.state()}
    mode = ex.policy.get("mode") or "off"
    wants_seal = mode == "force"
    dns_should_block = wants_seal and (ex.policy.get("dns") or "proxy") == "proxy"
    problems = []
    if wants_seal and not data.get("direct_blocked"):
        problems.append("VAZAMENTO: uma conexão direta saiu da máquina — as regras não "
                        "estão cobrindo o usuário dos comandos")
    if dns_should_block and not data.get("dns_blocked"):
        problems.append("VAZAMENTO DE DNS: o resolvedor local respondeu — nomes estão "
                        "sendo consultados fora do proxy")
    if mode != "off" and not data.get("proxy_ip"):
        problems.append("o proxy não respondeu: %s" % (data.get("proxy_error") or "sem detalhe"))
    return {
        "ok": not problems,
        "checks": data,
        "problems": problems,
        "egress": ex.state(),
        "verdict": ("saída selada pelo proxy" if not problems and wants_seal
                    else "sem vazamento detectado" if not problems
                    else "; ".join(problems)),
    }


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "aiw-remote-agent/" + AGENT_VERSION
    protocol_version = "HTTP/1.1"

    # injetados pelo serve()
    cfg: Dict[str, Any] = {}
    ex: Optional[Executor] = None
    cfg_path: str = DEFAULT_CONFIG_PATH

    def log_message(self, fmt, *args):  # silencia o log padrão (vai para o logging)
        log.info("%s %s", self.address_string(), fmt % args)

    # -- helpers ------------------------------------------------------------ #
    def _send(self, code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _authorized(self) -> bool:
        expected = (self.cfg.get("token") or "").strip()
        if not expected:
            return False
        got = (self.headers.get("Authorization") or "").strip()
        if got.lower().startswith("bearer "):
            got = got[7:].strip()
        # comparação em tempo constante: um token adivinhado byte a byte é um ataque
        # barato contra um serviço que fica exposto na internet.
        return hmac.compare_digest(got, expected)

    def _allowed_peer(self) -> bool:
        cidrs = self.cfg.get("allow_cidrs") or []
        if not cidrs:
            return True
        try:
            import ipaddress
            ip = ipaddress.ip_address(self.client_address[0])
            return any(ip in ipaddress.ip_network(c, strict=False) for c in cidrs)
        except Exception:  # noqa: BLE001
            return False

    def _body(self) -> Dict[str, Any]:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if n <= 0:
            return {}
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    # -- roteamento --------------------------------------------------------- #
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if not self._allowed_peer():
            self._send(403, {"error": "origem não permitida"})
            return
        if path == "/v1/health":  # sem token: só para o systemd/monitor externo
            self._send(200, {"ok": True, "agent": "aiw-remote-agent", "version": AGENT_VERSION})
            return
        if not self._authorized():
            self._send(401, {"error": "token inválido"})
            return
        ex = self.ex
        assert ex is not None
        query = parse_qs(parsed.query or "")
        try:
            if method == "GET" and path == "/v1/ping":
                self._send(200, self._ping())
            elif method == "POST" and path == "/v1/exec":
                b = self._body()
                cmd = (b.get("command") or "").strip()
                if not cmd:
                    self._send(400, {"error": "comando vazio"})
                    return
                self._send(200, ex.run(cmd, cwd=b.get("cwd") or "",
                                       timeout=int(b.get("timeout") or 120),
                                       env=b.get("env") or {}, shell=b.get("shell") or ""))
            elif method == "POST" and path == "/v1/jobs":
                b = self._body()
                cmd = (b.get("command") or "").strip()
                if not cmd:
                    self._send(400, {"error": "comando vazio"})
                    return
                self._send(200, ex.start_job(cmd, cwd=b.get("cwd") or "",
                                             env=b.get("env") or {}, shell=b.get("shell") or ""))
            elif method == "GET" and path == "/v1/jobs":
                self._send(200, ex.list_jobs())
            elif method == "GET" and path.startswith("/v1/jobs/"):
                jid = path.split("/")[-1]
                wait = int((query.get("wait") or ["0"])[0] or 0)
                self._send(200, ex.get_job(jid, wait=wait))
            elif method == "POST" and path.startswith("/v1/jobs/") and path.endswith("/kill"):
                self._send(200, ex.kill(path.split("/")[-2]))
            elif method == "GET" and path == "/v1/egress":
                self._send(200, {"ok": True, "egress": ex.state()})
            elif method == "PUT" and path == "/v1/egress":
                self._send(200, self._set_egress(self._body()))
            elif method == "POST" and path == "/v1/egress/test":
                self._send(200, leak_test(ex))
            else:
                self._send(404, {"error": "rota desconhecida: %s %s" % (method, path)})
        except Exception as exc:  # noqa: BLE001 - nunca derruba o agente
            log.exception("erro tratando %s %s", method, path)
            self._send(500, {"error": "erro interno do agente: %s" % str(exc)[:200]})

    def _ping(self) -> Dict[str, Any]:
        ex = self.ex
        assert ex is not None
        uname = os.uname()
        uid = ex.run_uid()
        return {
            "ok": True,
            "agent": "aiw-remote-agent",
            "version": AGENT_VERSION,
            "protocol": PROTOCOL,
            "hostname": socket.gethostname(),
            "os": "%s %s" % (uname.sysname, uname.release),
            "kernel": uname.version,
            "arch": uname.machine,
            "agent_user": pwd.getpwuid(geteuid()).pw_name if pwd else "",
            "root": geteuid() == 0,
            "run_user": ex.cfg.get("run_user") or "",
            "run_uid": uid,
            "shell": ex.cfg.get("shell") or "",
            "workdir": ex.cfg.get("workdir") or _home_of(uid),
            "egress": ex.state(),
            "uptime": round(time.time() - START_TIME, 1),
        }

    def _set_egress(self, body: Dict[str, Any]) -> Dict[str, Any]:
        ex = self.ex
        assert ex is not None
        pol = dict(DEFAULT_EGRESS)
        pol.update(ex.policy)
        for key in ("mode", "proxy_url", "dns", "killswitch", "allow_lan", "allow_hosts"):
            if key in body:
                pol[key] = body[key]
        if pol.get("mode") not in ("off", "env", "force"):
            return {"ok": False, "error": "modo inválido (use off, env ou force)"}
        if pol.get("mode") != "off":
            try:
                proxy_parts(pol.get("proxy_url") or "")
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
        ex.cfg["egress"] = pol
        state = ex.apply_policy()
        save_config(self.cfg_path, ex.cfg)
        applied = state["status"] in ("enforced", "env-only", "open")
        return {
            "ok": applied,
            "applied": state["status"] == "enforced",
            "status": state["status"],
            "egress": state,
            "warning": ("" if state["status"] != "degraded" else
                        "política NÃO aplicada: %s%s" % (
                            state["detail"],
                            " — com killswitch ligado, os comandos serão recusados até "
                            "isto ser resolvido." if state["killswitch"] else "")),
        }


START_TIME = time.time()


def serve(cfg_path: str) -> None:
    cfg = load_config(cfg_path)
    if not (cfg.get("token") or "").strip():
        raise SystemExit("config sem token — rode o instalador ou defina 'token' em " + cfg_path)
    ex = Executor(cfg)
    state = ex.apply_policy()
    log.info("política de saída: %s (%s)", state["status"], state.get("detail") or "ok")
    if state["status"] == "degraded" and state["killswitch"]:
        log.warning("KILLSWITCH ATIVO: comandos serão recusados até a política subir")

    Handler.cfg, Handler.ex, Handler.cfg_path = cfg, ex, cfg_path
    addr = (cfg.get("bind") or "0.0.0.0", int(cfg.get("port") or DEFAULT_PORT))
    httpd = ThreadingHTTPServer(addr, Handler)
    cert, key = (cfg.get("tls_cert") or "").strip(), (cfg.get("tls_key") or "").strip()
    if cert and key and os.path.exists(cert) and os.path.exists(key):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cert, key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"
    else:
        scheme = "http"
        log.warning("SEM TLS: o token trafega em claro. Gere o certificado (o instalador "
                    "faz isso) ou exponha o agente só por um túnel.")
    log.info("agente %s ouvindo em %s://%s:%s", AGENT_VERSION, scheme,
             cfg.get("bind"), cfg.get("port"))

    def _bye(_sig, _frm):
        log.info("encerrando; removendo regras de firewall do agente")
        clear_firewall(ex.run_uid())
        httpd.shutdown()

    signal.signal(signal.SIGTERM, _bye)
    signal.signal(signal.SIGINT, _bye)
    try:
        httpd.serve_forever()
    finally:
        clear_firewall(ex.run_uid())


def main() -> None:
    ap = argparse.ArgumentParser(description="AI Workspace — Remote Terminal Agent")
    ap.add_argument("--config", default=os.environ.get("AIW_AGENT_CONFIG", DEFAULT_CONFIG_PATH))
    ap.add_argument("--print-token", action="store_true", help="mostra o token e sai")
    ap.add_argument("--status", action="store_true", help="mostra a política de saída e sai")
    ap.add_argument("--test-egress", action="store_true", help="roda o teste de vazamento e sai")
    ap.add_argument("--new-token", action="store_true", help="gera e grava um token novo")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if args.new_token:
        cfg = load_config(args.config)
        cfg["token"] = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
        save_config(args.config, cfg)
        print(cfg["token"])
        return
    if args.print_token:
        print((load_config(args.config).get("token") or "").strip())
        return
    if args.status or args.test_egress:
        ex = Executor(load_config(args.config))
        ex.apply_policy()
        print(json.dumps(leak_test(ex) if args.test_egress else ex.state(),
                         indent=2, ensure_ascii=False))
        return
    serve(args.config)


if __name__ == "__main__":
    main()
