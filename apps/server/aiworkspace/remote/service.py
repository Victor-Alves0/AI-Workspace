"""Regras do Remote Terminal: resolver a máquina, aplicar política, executar.

Duas entradas com necessidades diferentes:

* as ROTAS (`remote_routes.py`) já têm sessão do request e passam `db`;
* a FERRAMENTA (SIFT) roda numa thread do pool, síncrona, sem sessão — chama as
  funções `*_standalone`, que abrem um engine próprio com `NullPool` e o descartam,
  exatamente como o Codespace faz em `graph_service.load_project`. Reaproveitar o
  engine do app aqui levaria conexões para OUTRO event loop.

O dono é verificado em TODA leitura de host, inclusive nas versões standalone: a
ferramenta nunca confia só no gating por-modelo para não tocar a máquina de outro
usuário — o mesmo princípio de defesa em profundidade do Codespace.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ..config import get_settings
from ..models import RemoteHost
from . import agent_client
from .agent_client import RemoteBlocked, RemoteError

logger = logging.getLogger(__name__)

# Modos da política de saída dos comandos NA MÁQUINA REMOTA (camada 2).
EGRESS_MODES = ("off", "env", "force")
DEFAULT_EGRESS: dict[str, Any] = {
    "mode": "off",
    "dns": "proxy",       # proxy = DNS resolvido pelo proxy; system = resolvedor local
    "killswitch": True,   # sem proxy utilizável, o comando NÃO roda
    "allow_lan": False,   # liberar 10/8, 172.16/12, 192.168/16 (acesso à rede interna)
    "allow_hosts": [],    # IPs extras liberados fora do proxy (ex.: o próprio painel)
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    s = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return (s or "host")[:80]


def normalize_egress(raw: dict | None) -> dict[str, Any]:
    """Sanitiza a política vinda da UI/tool para as chaves e valores que o agente
    entende. Desconhecido cai no padrão — nunca propagamos um modo inventado, que o
    agente traduziria em "sem restrição" e daria uma falsa sensação de proteção."""
    src = raw if isinstance(raw, dict) else {}
    mode = str(src.get("mode") or "off").lower()
    if mode not in EGRESS_MODES:
        mode = "off"
    dns = str(src.get("dns") or "proxy").lower()
    if dns not in ("proxy", "system"):
        dns = "proxy"
    hosts = [str(h).strip() for h in (src.get("allow_hosts") or []) if str(h).strip()][:20]
    return {
        "mode": mode,
        "dns": dns,
        "killswitch": src.get("killswitch", True) is not False,
        "allow_lan": bool(src.get("allow_lan")),
        "allow_hosts": hosts,
    }


def host_cfg(row: RemoteHost) -> dict[str, Any]:
    """Dict DECIFRADO que o agent_client consome. Só na memória — nada aqui pode ir
    para log, trace ou resposta de API (token e proxy carregam credenciais)."""
    return {
        "base_url": row.base_url or "",
        "token": row.token or "",
        "tls_mode": row.tls_mode or "pinned",
        "tls_cert_pem": row.tls_cert_pem or "",
        "proxy_url": row.proxy_url or "",
        "require_proxy": bool(row.require_proxy),
    }


def public_view(row: RemoteHost, *, with_secrets: bool = False) -> dict[str, Any]:
    """Como a UI e o modelo veem a máquina. O token NUNCA sai daqui (só o fato de
    existir); a URL do proxy sai MASCARADA porque costuma trazer usuário:senha."""
    proxy = (row.proxy_url or "").strip()
    egress_proxy = (row.egress_proxy or "").strip()
    out: dict[str, Any] = {
        "id": str(row.id),
        "name": row.name,
        "slug": row.slug,
        "base_url": row.base_url,
        "enabled": bool(row.enabled),
        "tls_mode": row.tls_mode,
        "has_cert": bool((row.tls_cert_pem or "").strip()),
        "has_token": bool((row.token or "").strip()),
        "proxy": mask_url(proxy),
        "has_proxy": bool(proxy),
        "require_proxy": bool(row.require_proxy),
        "egress": normalize_egress(row.egress),
        "egress_proxy": mask_url(egress_proxy),
        "has_egress_proxy": bool(egress_proxy),
        "workdir": row.workdir or "",
        "shell": row.shell or "",
        "timeout_seconds": int(row.timeout_seconds or 120),
        "confirm_required": bool(row.confirm_required),
        "status": row.status or "unknown",
        "last_error": row.last_error or "",
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "agent_version": row.agent_version or "",
        "info": row.info or {},
    }
    if with_secrets:
        out["token"] = row.token or ""
    return out


def mask_url(url: str) -> str:
    """"socks5://user:senha@10.0.0.1:9050" -> "socks5://user:***@10.0.0.1:9050"."""
    raw = (url or "").strip()
    if not raw:
        return ""
    return re.sub(r"://([^:/@]+):([^@]+)@", r"://\1:***@", raw)


# --------------------------------------------------------------------------- #
# CRUD (rotas)
# --------------------------------------------------------------------------- #
async def list_hosts(db: AsyncSession, user_id: uuid.UUID) -> list[RemoteHost]:
    return list(await db.scalars(
        select(RemoteHost).where(RemoteHost.user_id == user_id).order_by(RemoteHost.created_at)
    ))


async def get_host(db: AsyncSession, user_id: uuid.UUID, host_id: str) -> RemoteHost | None:
    try:
        hid = uuid.UUID(str(host_id))
    except ValueError:
        return None
    row = await db.get(RemoteHost, hid)
    if row is None or row.user_id != user_id:
        return None
    return row


async def unique_slug(db: AsyncSession, user_id: uuid.UUID, base: str,
                      skip_id: uuid.UUID | None = None) -> str:
    """Slug livre para este usuário. O modelo escolhe a máquina POR SLUG — dois
    "vps" na mesma conta tornariam a escolha ambígua justamente numa ferramenta que
    roda comando com privilégio. Sufixa -2, -3… em vez de rejeitar."""
    base = slugify(base)
    rows = await list_hosts(db, user_id)
    taken = {r.slug for r in rows if r.id != skip_id}
    if base not in taken:
        return base
    for n in range(2, 100):
        cand = f"{base[:76]}-{n}"
        if cand not in taken:
            return cand
    return f"{base[:70]}-{uuid.uuid4().hex[:6]}"


async def refresh_status(db: AsyncSession, row: RemoteHost) -> dict[str, Any]:
    """Faz o ping e PERSISTE o desfecho (online/offline/blocked/unauthorized).

    Guardar o status é o que faz o painel e o modelo saberem que a máquina caiu sem
    cada um ter que descobrir na própria falha. `blocked` é registrado separado de
    `offline`: significa que a política de rede impediu a saída daqui — a máquina pode
    estar perfeitamente no ar, e confundir os dois manda o usuário depurar a VPS
    quando o problema é o proxy do workspace.
    """
    cfg = host_cfg(row)
    try:
        data = await agent_client.ping(cfg)
    except RemoteBlocked as exc:
        row.status, row.last_error = "blocked", str(exc)[:500]
        await db.commit()
        return {"ok": False, "status": "blocked", "error": str(exc)}
    except RemoteError as exc:
        msg = str(exc)
        row.status = "unauthorized" if "token" in msg.lower() else "offline"
        row.last_error = msg[:500]
        await db.commit()
        return {"ok": False, "status": row.status, "error": msg}
    row.status = "online"
    row.last_error = None
    row.last_seen_at = datetime.now(timezone.utc)
    row.agent_version = str(data.get("version") or "")[:32]
    row.info = {k: v for k, v in data.items() if k not in ("ok",)}
    await db.commit()
    return {"ok": True, "status": "online", "info": row.info}


async def apply_egress(db: AsyncSession, row: RemoteHost) -> dict[str, Any]:
    """Empurra a política de saída DOS COMANDOS para o agente (camada 2).

    O proxy da máquina remota viaja aqui — é o único momento em que ele sai do banco.
    A resposta do agente diz `applied` (firewall no ar) ou `degraded` + motivo (sem
    root, sem nft/iptables); repassamos o motivo cru, porque "degradado" com killswitch
    ligado significa que os comandos vão passar a ser RECUSADOS, e o usuário precisa
    entender por quê antes de achar que a máquina quebrou.
    """
    policy = normalize_egress(row.egress)
    policy["proxy_url"] = (row.egress_proxy or "").strip()
    if policy["mode"] != "off" and not policy["proxy_url"]:
        return {"ok": False, "error": "defina o proxy de saída da máquina antes de "
                                      f"ativar o modo '{policy['mode']}'"}
    try:
        data = await agent_client.set_egress(host_cfg(row), policy)
    except (RemoteError, RemoteBlocked) as exc:
        return {"ok": False, "error": str(exc)}
    if isinstance(row.info, dict):
        row.info = {**row.info, "egress": data.get("egress") or {}}
        await db.commit()
    return {"ok": True, **data}


# --------------------------------------------------------------------------- #
# Acesso standalone (ferramenta SIFT — contexto síncrono, sem sessão do request)
# --------------------------------------------------------------------------- #
def _engine():
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


async def resolve_standalone(user_id: str, ref: str) -> tuple[dict[str, Any] | None, str]:
    """Acha a máquina por slug, nome ou id e devolve (dados, erro).

    `ref` vazio resolve para a ÚNICA máquina ativa quando só existe uma — com duas ou
    mais devolve a lista e exige escolha, em vez de chutar: rodar `rm -rf` na VPS
    errada não é um erro recuperável.

    O dict devolvido junta a config decifrada e os metadados que a tool precisa
    (nome, timeout, confirmação) para não abrir sessão duas vezes.
    """
    eng = _engine()
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            try:
                uid = uuid.UUID(str(user_id))
            except (ValueError, TypeError):
                return None, "sem usuário no contexto"
            rows = [r for r in await list_hosts(db, uid) if r.enabled]
            if not rows:
                return None, ("nenhuma máquina remota conectada. O usuário adiciona uma em "
                              "Configurações → Integrações → Remote Terminal (instala o agente "
                              "na VPS e cola endereço + token).")
            key = (ref or "").strip().lower()
            if not key:
                if len(rows) == 1:
                    return _row_payload(rows[0]), ""
                names = ", ".join(f"{r.slug} ({r.name})" for r in rows)
                return None, f"há mais de uma máquina: informe 'host'. Disponíveis: {names}"
            for r in rows:
                if key in (r.slug.lower(), r.name.strip().lower(), str(r.id).lower()):
                    return _row_payload(r), ""
            names = ", ".join(r.slug for r in rows)
            return None, f"máquina '{ref}' não encontrada. Disponíveis: {names}"
    finally:
        await eng.dispose()


def _row_payload(row: RemoteHost) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "slug": row.slug,
        "cfg": host_cfg(row),
        "workdir": row.workdir or "",
        "shell": row.shell or "",
        "timeout_seconds": int(row.timeout_seconds or 120),
        "confirm_required": bool(row.confirm_required),
        "egress": normalize_egress(row.egress),
        "status": row.status or "unknown",
    }


async def list_standalone(user_id: str) -> list[dict[str, Any]]:
    """Máquinas ativas do usuário, sem segredo — para a ação `hosts` da ferramenta."""
    eng = _engine()
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            try:
                uid = uuid.UUID(str(user_id))
            except (ValueError, TypeError):
                return []
            out = []
            for r in await list_hosts(db, uid):
                if not r.enabled:
                    continue
                eg = normalize_egress(r.egress)
                out.append({
                    "host": r.slug,
                    "name": r.name,
                    "status": r.status or "unknown",
                    "workdir": r.workdir or "",
                    "egress_mode": eg["mode"],
                    "killswitch": eg["killswitch"],
                    "os": (r.info or {}).get("os") or "",
                    "hostname": (r.info or {}).get("hostname") or "",
                    "root": bool((r.info or {}).get("root")),
                    "last_seen": r.last_seen_at.isoformat() if r.last_seen_at else None,
                })
            return out
    finally:
        await eng.dispose()


async def mark_seen_standalone(host_id: str, ok: bool, error: str = "") -> None:
    """Atualiza o status a partir do resultado de uma chamada da FERRAMENTA.

    Sem isto o painel só saberia da queda no próximo teste manual, enquanto o modelo já
    teria batido de frente com ela. Nunca levanta: registrar o estado é secundário à
    resposta da tool que o usuário está esperando."""
    eng = _engine()
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            try:
                row = await db.get(RemoteHost, uuid.UUID(str(host_id)))
            except (ValueError, TypeError):
                return
            if row is None:
                return
            if ok:
                row.status, row.last_error = "online", None
                row.last_seen_at = datetime.now(timezone.utc)
            else:
                low = error.lower()
                row.status = ("blocked" if "killswitch" in low or "proxy" in low
                              else "unauthorized" if "token" in low else "offline")
                row.last_error = error[:500]
            await db.commit()
    except Exception as exc:  # noqa: BLE001 - telemetria de status não quebra a tool
        logger.debug("remote: falha ao marcar status (%s)", exc)
    finally:
        await eng.dispose()
