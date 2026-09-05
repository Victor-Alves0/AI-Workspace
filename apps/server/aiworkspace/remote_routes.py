"""Rotas do Remote Terminal (máquinas remotas com agente instalado).

Uma máquina remota é uma CONEXÃO do usuário, como as demais integrações: o dono é
sempre `require_approved` e cada acesso revalida a posse antes de tocar no host.

Segredos entram por PATCH parcial (campo vazio = "mantém o atual") e nunca voltam:
`public_view` devolve só o fato de existirem, e as URLs de proxy saem mascaradas
porque quase sempre carregam usuário:senha. Um formulário que reexibisse o token o
espalharia por cache do navegador, screenshot e log de erro do front.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved
from .db import get_db
from .models import RemoteHost, User
from .remote import agent_client, service
from .remote.agent_client import RemoteBlocked, RemoteError
from .tools import sift_service

router = APIRouter(prefix="/remote", tags=["remote"])


class HostIn(BaseModel):
    name: str | None = None
    base_url: str | None = None
    token: str | None = None            # vazio = mantém
    tls_mode: str | None = None         # pinned | system | off
    tls_cert_pem: str | None = None
    proxy_url: str | None = None        # vazio = mantém; "-" = remove
    require_proxy: bool | None = None
    egress: dict | None = None
    egress_proxy: str | None = None     # vazio = mantém; "-" = remove
    workdir: str | None = None
    shell: str | None = None
    timeout_seconds: int | None = None
    confirm_required: bool | None = None
    enabled: bool | None = None


class ExecIn(BaseModel):
    command: str
    cwd: str | None = None
    timeout: int | None = None


async def _owned_host(db: AsyncSession, user: User, host_id: str) -> RemoteHost:
    """A máquina, SÓ se pertence a quem pediu. Todo handler com {host_id} no path
    passa por aqui — um id de outro usuário responde 404, não a máquina dele."""
    row = await service.get_host(db, user.id, host_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Máquina não encontrada")
    return row


def _secret_patch(current: str | None, incoming: str | None) -> str | None:
    """Campo secreto num PATCH: None/"" mantém, "-" apaga, resto substitui.

    O sentinela existe porque "" já significa "o formulário não mexeu nisso" — sem um
    valor explícito para apagar, não haveria como REMOVER um proxy salvo, e o usuário
    ficaria preso a uma rota que quis desfazer."""
    if incoming is None or incoming == "":
        return current
    if incoming.strip() == "-":
        return None
    return incoming.strip()


@router.get("/hosts")
async def list_hosts(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    rows = await service.list_hosts(db, user.id)
    return {"hosts": [service.public_view(r) for r in rows]}


@router.post("/hosts", status_code=status.HTTP_201_CREATED)
async def create_host(
    body: HostIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    name = (body.name or "").strip()
    base_url = (body.base_url or "").strip()
    token = (body.token or "").strip()
    if not name or not base_url or not token:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Nome, endereço e token são obrigatórios")
    row = RemoteHost(
        user_id=user.id,
        name=name[:120],
        slug=await service.unique_slug(db, user.id, name),
        base_url=base_url,
        token=token,
        tls_mode=(body.tls_mode or "pinned"),
        tls_cert_pem=(body.tls_cert_pem or "").strip() or None,
        proxy_url=(body.proxy_url or "").strip() or None,
        require_proxy=bool(body.require_proxy),
        egress=service.normalize_egress(body.egress),
        egress_proxy=(body.egress_proxy or "").strip() or None,
        workdir=(body.workdir or "").strip(),
        shell=(body.shell or "").strip(),
        timeout_seconds=int(body.timeout_seconds or 120),
        confirm_required=body.confirm_required is not False,
        enabled=body.enabled is not False,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    sift_service.invalidate(str(user.id))
    # ping imediato: um endereço/token errado tem que aparecer AGORA, no formulário
    # aberto, e não daqui a dois dias quando o modelo tentar usar a máquina.
    await service.refresh_status(db, row)
    return service.public_view(row)


@router.put("/hosts/{host_id}")
async def update_host(
    host_id: str, body: HostIn,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    row = await _owned_host(db, user, host_id)
    if body.name is not None and body.name.strip():
        row.name = body.name.strip()[:120]
        row.slug = await service.unique_slug(db, user.id, row.name, skip_id=row.id)
    if body.base_url is not None and body.base_url.strip():
        row.base_url = body.base_url.strip()
    row.token = _secret_patch(row.token, body.token) or ""
    if body.tls_mode in ("pinned", "system", "off"):
        row.tls_mode = body.tls_mode
    if body.tls_cert_pem is not None:
        row.tls_cert_pem = body.tls_cert_pem.strip() or None
    row.proxy_url = _secret_patch(row.proxy_url, body.proxy_url)
    row.egress_proxy = _secret_patch(row.egress_proxy, body.egress_proxy)
    if body.require_proxy is not None:
        row.require_proxy = bool(body.require_proxy)
    if body.egress is not None:
        row.egress = service.normalize_egress(body.egress)
    if body.workdir is not None:
        row.workdir = body.workdir.strip()
    if body.shell is not None:
        row.shell = body.shell.strip()
    if body.timeout_seconds is not None:
        row.timeout_seconds = max(5, min(int(body.timeout_seconds), 900))
    if body.confirm_required is not None:
        row.confirm_required = bool(body.confirm_required)
    if body.enabled is not None:
        row.enabled = bool(body.enabled)
    await db.commit()
    sift_service.invalidate(str(user.id))
    return service.public_view(row)


@router.delete("/hosts/{host_id}")
async def delete_host(
    host_id: str, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    row = await _owned_host(db, user, host_id)
    await db.delete(row)
    await db.commit()
    sift_service.invalidate(str(user.id))
    return {"ok": True}


@router.post("/hosts/{host_id}/test")
async def test_host(
    host_id: str, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    row = await _owned_host(db, user, host_id)
    return await service.refresh_status(db, row)


@router.post("/hosts/{host_id}/egress")
async def push_egress(
    host_id: str, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Aplica NA MÁQUINA a política de saída já salva na conexão."""
    row = await _owned_host(db, user, host_id)
    return await service.apply_egress(db, row)


@router.post("/hosts/{host_id}/egress/test")
async def egress_leak_test(
    host_id: str, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Teste de vazamento medido de DENTRO da máquina: sai pelo proxy, tenta sair
    direto e tenta resolver DNS. O sucesso é o direto FALHAR."""
    row = await _owned_host(db, user, host_id)
    try:
        return await agent_client.test_egress(service.host_cfg(row))
    except (RemoteError, RemoteBlocked) as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/hosts/{host_id}/exec")
async def exec_on_host(
    host_id: str, body: ExecIn,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Terminal do painel: o usuário roda um comando direto, sem passar pelo modelo."""
    row = await _owned_host(db, user, host_id)
    command = (body.command or "").strip()
    if not command:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Comando vazio")
    timeout = max(5, min(int(body.timeout or row.timeout_seconds or 120), 900))
    try:
        data: dict[str, Any] = await agent_client.exec_command(
            service.host_cfg(row), command,
            cwd=(body.cwd or row.workdir or ""), timeout=timeout, shell=row.shell or "",
        )
    except (RemoteError, RemoteBlocked) as exc:
        await service.mark_seen_standalone(str(row.id), False, str(exc))
        return {"error": str(exc)}
    await service.mark_seen_standalone(str(row.id), True)
    return data


@router.get("/install")
async def install_help(user: User = Depends(require_approved)):
    """Instruções de instalação do agente (mostradas no painel)."""
    return {
        "agent_path": "apps/remote-agent/aiw_remote_agent.py",
        "steps": [
            "Copie apps/remote-agent/ para a máquina (scp -r apps/remote-agent root@SEU_IP:/tmp/).",
            "Na máquina: cd /tmp/remote-agent && sudo ./install.sh --san SEU_IP",
            ("Para selar a saída pelo proxy: sudo ./install.sh --san SEU_IP "
             "--proxy socks5h://127.0.0.1:9050 --force-egress"),
            "Cole aqui o endereço, o token e o certificado que o instalador imprimiu.",
        ],
        "default_port": 8791,
    }
