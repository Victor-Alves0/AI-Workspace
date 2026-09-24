"""WhatsApp por QR Code: escolhe o motor da instalação.

  local      o whatsmeow embutido via neonize (integrations/whatsapp_local) — o padrão no
             Docker e no desktop: sem serviço separado (a Evolution API saiu do compose)
  evolution  uma Evolution API EXTERNA, só se pedida explicitamente
  auto       (padrão) o local; a Evolution só se o neonize não estiver instalado

Os dois têm a MESMA interface; quem chama importa este módulo e não sabe qual roda.
A escolha é da instalação (WHATSAPP_QR_BACKEND), não da conexão: trocar de motor
depois exige escanear o QR de novo.
"""
from __future__ import annotations

from typing import Any

from ..config import get_settings
from . import whatsapp_evolution as _evolution
from . import whatsapp_local as _local


def backend() -> str:
    """'evolution' | 'local' | '' (nenhum disponível)."""
    modo = (get_settings().whatsapp_qr_backend or "auto").strip().lower()
    if modo == "evolution":
        return "evolution" if _evolution.configured() else ""
    if modo == "local":
        return "local" if _local.available() else ""
    # auto: o embutido primeiro. Antes a Evolution tinha prioridade quando havia
    # EVOLUTION_API_KEY — instalações antigas ainda têm a chave no .env e apontariam
    # para um container que não existe mais.
    if _local.available():
        return "local"
    return "evolution" if _evolution.configured() else ""


def _impl():
    return _local if backend() == "local" else _evolution


def configured() -> bool:
    return bool(backend())


def unavailable_reason() -> str:
    modo = (get_settings().whatsapp_qr_backend or "auto").strip().lower()
    if modo == "local":
        return "o motor local do WhatsApp (neonize) não está instalado nesta máquina."
    if modo == "evolution":
        return ("WHATSAPP_QR_BACKEND=evolution, mas a Evolution API não está configurada "
                "(EVOLUTION_API_URL / EVOLUTION_API_KEY).")
    return ("WhatsApp por QR indisponível: o motor embutido (neonize) não está instalado "
            "nesta instalação.")


async def create_instance(instance: str, webhook_url: str) -> dict[str, Any]:
    return await _impl().create_instance(instance, webhook_url)


async def get_qr(instance: str) -> dict[str, Any]:
    return await _impl().get_qr(instance)


async def get_state(instance: str) -> str:
    return await _impl().get_state(instance)


async def get_profile(instance: str) -> dict[str, str]:
    return await _impl().get_profile(instance)


async def send_text(instance: str, jid: str, text: str, delay_ms: int = 0) -> dict[str, Any]:
    return await _impl().send_text(instance, jid, text, delay_ms)


async def send_media(instance: str, jid: str, data: bytes, mime: str,
                     filename: str, caption: str = "") -> dict[str, Any]:
    return await _impl().send_media(instance, jid, data, mime, filename, caption)


async def send_presence(instance: str, jid: str, presence: str = "composing", delay_ms: int = 3000) -> None:
    await _impl().send_presence(instance, jid, presence, delay_ms)


async def delete_instance(instance: str) -> None:
    await _impl().delete_instance(instance)


async def get_media_base64(instance: str, msg_id: str) -> tuple[str, str]:
    return await _impl().get_media_base64(instance, msg_id)


async def find_chats(instance: str, limit: int = 50) -> list[dict[str, str]]:
    return await _impl().find_chats(instance, limit)


async def find_contacts(instance: str, query: str = "", limit: int = 30) -> list[dict[str, str]]:
    return await _impl().find_contacts(instance, query, limit)


async def find_messages(instance: str, jid: str, limit: int = 20) -> list[dict[str, Any]]:
    return await _impl().find_messages(instance, jid, limit)


def parse_webhook(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Só existe webhook na Evolution (o motor local entrega direto)."""
    return _evolution.parse_webhook(payload)
