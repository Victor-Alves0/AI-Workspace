"""Cliente HTTP do agente remoto — a CAMADA 1 da rede do Remote Terminal.

Aqui mora a única saída do workspace em direção à máquina do usuário, e é aqui que a
promessa "sempre falar com X usando Y" é cumprida ou o pedido é RECUSADO. As duas
regras que valem mais que qualquer conveniência:

  * **Sem fallback.** Se o host exige proxy (`require_proxy`) e não há proxy utilizável,
    a chamada levanta `RemoteBlocked` — nunca sai direto. Um retry "só dessa vez" pela
    rota direta entregaria ao outro lado exatamente o IP que o proxy existia para
    esconder, e o usuário não teria como saber que aconteceu.
  * **DNS junto com o tráfego.** O endereço do agente é resolvido PELO PROXY: mandamos
    o hostname para o proxy em vez de resolvê-lo aqui (é o comportamento nativo de
    SOCKS5 e do CONNECT em proxy HTTP no httpx). Resolver localmente vazaria o nome do
    alvo para o resolvedor do servidor mesmo com o tráfego tunelado — o vazamento de
    DNS clássico. Por isso `socks5h://` é aceito e normalizado para `socks5://`: no
    httpx o "h" já é o padrão, e recusar o esquema só faria o usuário achar que não dá.

TLS: o agente costuma usar certificado AUTOASSINADO (é a máquina do usuário, não um
site). O modo padrão `pinned` valida contra aquele certificado específico, colado na
configuração do host — verificação real de chave, sem depender de CA pública nem de o
SAN bater com o IP/túnel pelo qual se chega. `system` é para agente atrás de proxy
reverso com certificado público; `off` só se faz sentido dentro de um túnel confiável.
"""

from __future__ import annotations

import logging
import ssl
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

logger = logging.getLogger(__name__)

# Versão do contrato falado com o agente. O agente devolve a dele no /v1/ping; uma
# diferença de MAIOR significa protocolo incompatível (avisamos em vez de falhar torto).
PROTOCOL = 1

_DEFAULT_TIMEOUT = 20.0


class RemoteError(Exception):
    """Falha ao falar com o agente (rede, TLS, HTTP, resposta inválida)."""


class RemoteBlocked(RemoteError):
    """A chamada foi RECUSADA aqui, antes de sair: a política de rede não permitia.

    É o killswitch do lado do workspace. Separado de `RemoteError` porque o significado
    para o usuário é oposto: não é "a máquina não respondeu", é "não deixamos perguntar".
    """


def normalize_proxy(url: str | None) -> str:
    """URL de proxy pronta para o httpx, ou "" se não houver.

    Aceita `socks5h://` (o "h" = DNS resolvido no proxy) e devolve `socks5://`, que no
    httpx já resolve remotamente. Levanta `RemoteBlocked` num esquema que não tuneliza —
    melhor recusar alto do que sair por um caminho que o usuário achou que era proxy.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme == "socks5h":
        scheme = "socks5"
    if scheme not in ("http", "https", "socks5"):
        raise RemoteBlocked(
            f"esquema de proxy não suportado: '{parsed.scheme}'. Use socks5://, "
            "socks5h:// (DNS no proxy), http:// ou https://"
        )
    if not parsed.hostname:
        raise RemoteBlocked("URL de proxy sem host (ex.: socks5://127.0.0.1:9050)")
    return urlunparse((scheme, parsed.netloc, parsed.path or "", "", "", ""))


def ssl_context(tls_mode: str, cert_pem: str | None) -> ssl.SSLContext | bool:
    """Contexto TLS conforme o modo do host. Devolve False só no modo "off"."""
    mode = (tls_mode or "pinned").lower()
    if mode == "off":
        return False
    if mode == "system":
        return ssl.create_default_context()
    pem = (cert_pem or "").strip()
    if not pem:
        raise RemoteBlocked(
            "TLS no modo 'pinned' exige o certificado do agente — copie o bloco "
            "-----BEGIN CERTIFICATE----- que o instalador imprimiu e cole na conexão "
            "(ou troque o modo de TLS)"
        )
    ctx = ssl.create_default_context()
    # `check_hostname=False` + CERT_REQUIRED com ESTE certificado como única âncora:
    # a chave precisa bater exatamente (pinagem), mas o SAN não precisa casar com o
    # endereço — o agente é alcançado ora pelo IP, ora por um túnel, ora por 127.0.0.1.
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    try:
        ctx.load_verify_locations(cadata=pem)
    except ssl.SSLError as exc:
        raise RemoteBlocked(f"certificado do agente inválido: {exc}") from exc
    return ctx


def _client(host: dict[str, Any], timeout: float) -> httpx.AsyncClient:
    """AsyncClient já com TLS e proxy do host aplicados (e o killswitch checado)."""
    proxy = normalize_proxy(host.get("proxy_url"))
    if host.get("require_proxy") and not proxy:
        raise RemoteBlocked(
            "killswitch: esta máquina exige sair por proxy e nenhum está configurado. "
            "Defina o proxy da conexão (ou desligue 'exigir proxy') — a conexão direta "
            "não é feita porque exporia o IP real do servidor."
        )
    verify = ssl_context(host.get("tls_mode") or "pinned", host.get("tls_cert_pem"))
    kwargs: dict[str, Any] = {"timeout": timeout, "verify": verify, "follow_redirects": False}
    if proxy:
        kwargs["proxy"] = proxy
    try:
        return httpx.AsyncClient(**kwargs)
    except ImportError as exc:  # socksio ausente num build antigo
        raise RemoteBlocked(
            f"proxy SOCKS indisponível neste servidor ({exc}). Instale o extra "
            "httpx[socks] ou use um proxy http://"
        ) from exc


def _url(base_url: str, path: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise RemoteBlocked("conexão sem endereço do agente (base_url)")
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    return f"{base}/v1/{path.lstrip('/')}"


async def call(
    host: dict[str, Any],
    method: str,
    path: str,
    *,
    json: dict | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Uma chamada ao agente. Devolve o JSON da resposta; levanta em qualquer falha.

    `host` é o dict já DECIFRADO do RemoteHost (token e proxy em claro na memória, nunca
    em log). Erros HTTP viram `RemoteError` com o corpo truncado — o agente responde
    `{"error": ...}` legível nos casos previstos (comando bloqueado, killswitch ativo).
    """
    tmo = float(timeout or _DEFAULT_TIMEOUT)
    token = (host.get("token") or "").strip()
    if not token:
        raise RemoteBlocked("conexão sem token do agente")
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "aiworkspace-remote/1"}
    url = _url(host.get("base_url") or "", path)
    async with _client(host, tmo) as client:
        try:
            r = await client.request(method.upper(), url, json=json, headers=headers)
        except httpx.ProxyError as exc:
            raise RemoteError(
                f"o proxy configurado não aceitou a conexão ({exc}). Nada foi enviado "
                "direto — verifique se o túnel/proxy está no ar."
            ) from exc
        except httpx.ConnectError as exc:
            raise RemoteError(
                f"não consegui conectar no agente ({exc}). Verifique o endereço, a porta "
                "e se o serviço aiw-remote-agent está rodando."
            ) from exc
        except httpx.HTTPError as exc:
            raise RemoteError(f"falha de rede falando com o agente: {exc}") from exc
    if r.status_code in (401, 403):
        raise RemoteError("o agente recusou o token (401/403) — gere um novo token no "
                          "instalador e atualize a conexão")
    if r.status_code >= 400:
        body = (r.text or "")[:300]
        raise RemoteError(f"o agente respondeu HTTP {r.status_code}: {body}")
    try:
        data = r.json()
    except ValueError as exc:
        raise RemoteError("o agente respondeu algo que não é JSON — o endereço aponta "
                          "para o serviço certo?") from exc
    if not isinstance(data, dict):
        raise RemoteError("resposta do agente em formato inesperado")
    return data


# --------------------------------------------------------------------------- #
# Operações (o vocabulário do agente; ver agent/aiw_remote_agent.py)
# --------------------------------------------------------------------------- #
async def ping(host: dict[str, Any]) -> dict[str, Any]:
    """Identidade e estado da máquina: SO, usuário, root?, política de saída ativa."""
    return await call(host, "GET", "ping", timeout=15.0)


async def exec_command(
    host: dict[str, Any], command: str, *, cwd: str = "", timeout: int = 120,
    env: dict | None = None, shell: str = "",
) -> dict[str, Any]:
    """Roda um comando e ESPERA. O cliente dá folga sobre o timeout do agente para
    distinguir "o comando estourou o tempo" (o agente responde timed_out) de "a rede
    caiu" (exceção aqui) — sem a folga os dois virariam o mesmo erro opaco."""
    payload = {"command": command, "cwd": cwd, "timeout": int(timeout),
               "env": env or {}, "shell": shell}
    return await call(host, "POST", "exec", json=payload, timeout=float(timeout) + 30.0)


async def start_job(host: dict[str, Any], command: str, *, cwd: str = "",
                    env: dict | None = None, shell: str = "") -> dict[str, Any]:
    payload = {"command": command, "cwd": cwd, "env": env or {}, "shell": shell}
    return await call(host, "POST", "jobs", json=payload, timeout=30.0)


async def job_status(host: dict[str, Any], job_id: str, *, wait: int = 0) -> dict[str, Any]:
    """Estado de um job. `wait` > 0 faz o AGENTE segurar a resposta até o job terminar
    (long-poll) — melhor que sondar daqui: cada sondagem seria uma volta pelo proxy."""
    q = f"jobs/{job_id}" + (f"?wait={int(wait)}" if wait else "")
    return await call(host, "GET", q, timeout=float(wait) + 30.0 if wait else 20.0)


async def list_jobs(host: dict[str, Any]) -> dict[str, Any]:
    return await call(host, "GET", "jobs", timeout=20.0)


async def kill_job(host: dict[str, Any], job_id: str) -> dict[str, Any]:
    return await call(host, "POST", f"jobs/{job_id}/kill", timeout=20.0)


async def get_egress(host: dict[str, Any]) -> dict[str, Any]:
    return await call(host, "GET", "egress", timeout=20.0)


async def set_egress(host: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Aplica a política de saída DOS COMANDOS na máquina (camada 2). O agente responde
    `applied` (regras de firewall no ar) ou `degraded` + motivo — nunca finge sucesso."""
    return await call(host, "PUT", "egress", json=policy, timeout=60.0)


async def test_egress(host: dict[str, Any]) -> dict[str, Any]:
    """Teste de VAZAMENTO na máquina remota: sai pelo proxy e tenta sair direto.
    O resultado que interessa é o segundo FALHAR — é a prova de que o killswitch morde."""
    return await call(host, "POST", "egress/test", timeout=90.0)
