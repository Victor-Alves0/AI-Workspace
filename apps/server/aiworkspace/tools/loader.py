"""Monta a visão SIFT de um chat a partir do banco (tools + segredos de busca).

Fluxo (SIFT v0.4):
  1. Uma instância `Sift` completa por usuário (builtins + tools dele), cacheada
     em sift_service — o índice de embeddings é construído uma vez só.
  2. O gating por modelo aplica `sift.scope(allow=[...])`: só as ferramentas
     marcadas no ModelConfig ficam visíveis/executáveis naquele chat
     ("deny wins"; fora da lista → recusado pelo próprio SIFT).

Sem modelo personalizado, com a chave SIFT desligada ou sem nenhuma ferramenta
marcada, retorna None (o chat segue sem ferramentas).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import GoogleAccount, Tool
from ..secrets_service import (
    ALPHAVANTAGE_KEY,
    BRAVE_KEY,
    FINNHUB_KEY,
    OPENROUTER_KEY,
    TAVILY_KEY,
    get_secret,
)
from . import sift_service

logger = logging.getLogger(__name__)

_BUILTIN_PREFIX = "builtin:"


def tool_config(model_config: Any | None) -> dict:
    """Config POR-MODELO das ferramentas (web_search, finance, deep_search,
    text_extraction), guardada em ModelConfig.filter_config['tools']."""
    fc = getattr(model_config, "filter_config", None) or {}
    tc = fc.get("tools") if isinstance(fc, dict) else None
    return tc if isinstance(tc, dict) else {}


def _tool_catalog(tool_ids: list[str], rows: list[Any]) -> list[str]:
    """Lista legível ("Nome — descrição") das ferramentas ativas do modelo.

    Injetada no system prompt para o modelo SABER o que existe — sem isso ele só
    enxerga as meta-ferramentas abstratas do SIFT e não descobre as tools.
    """
    builtins = {t["path"]: t for t in sift_service.BUILTIN_TOOLS}
    by_id = {str(t.id): t for t in rows}
    out: list[str] = []
    for tid in tool_ids or []:
        if not isinstance(tid, str) or not tid:
            continue
        if tid.startswith(_BUILTIN_PREFIX):
            info = builtins.get(tid[len(_BUILTIN_PREFIX):])
            if info:
                # o catálogo é lido pelo MODELO => descrição em inglês (model_desc)
                out.append(f"{info['name']} — {info.get('model_desc') or info['description']}")
        else:
            tool = by_id.get(tid)
            if tool is not None and tool.enabled:
                desc = (tool.description or "").strip()
                out.append(f"{tool.name or tool.path}" + (f" — {desc}" if desc else ""))
    return out


def _pinned_paths(pinned_ids: list[str], rows: list[Any]) -> list[str]:
    """Paths EXATOS (3 segmentos) das ferramentas fixadas, p/ o pin() do SIFT.

    Só retorna paths que de fato existem no índice (builtins sempre; tools do
    usuário se ativas e do tipo code) — evita KeyError no openai_tools().
    """
    builtins = {t["path"] for t in sift_service.BUILTIN_TOOLS}
    by_id = {str(t.id): t for t in rows}
    out: list[str] = []
    for tid in pinned_ids or []:
        if not isinstance(tid, str) or not tid:
            continue
        if tid.startswith(_BUILTIN_PREFIX):
            path = tid[len(_BUILTIN_PREFIX):]
            if path in builtins:
                out.append(path)
        else:
            tool = by_id.get(tid)
            if tool is not None and tool.enabled and getattr(tool, "tool_type", "code") != "mcp":
                out.append(sift_service.normalize_sift_path(tool.path))
    return sorted(set(out))


# Tools LONGAS (podem levar minutos) que não podem rodar via run_code: o watchdog
# de parede do sandbox (sift_code_timeout_seconds) mata o processo-filho e o
# resultado — que o pai continua computando e pagando — é DESCARTADO. No Modo
# Código, promovemos essas tools a specs de 1ª classe (nome flat, ex.:
# research__deep__run): o modelo as chama direto e elas rodam fora do sandbox.
_CODE_MODE_PROMOTE = ("research.deep.run",)


def _allow_match(path: str, allow: list[str]) -> bool:
    return any(path == a or (a.endswith(".*") and path.startswith(a[:-1])) for a in allow)


def _allow_patterns(tool_ids: list[str], rows: list[Any]) -> list[str]:
    """Converte a seleção do ModelConfig em padrões de allow do SIFT.

    - "builtin:<path>": path exato (3 segmentos) ou "<path>.*" p/ seleções
      antigas de 2 segmentos (ex.: 'web.search' salvo antes da normalização).
    - uuid de Tool: resolvido para o path normalizado da tool (se ativa).
    """
    by_id = {str(t.id): t for t in rows}
    patterns: list[str] = []
    for tid in tool_ids or []:
        if not isinstance(tid, str) or not tid:
            continue
        if tid.startswith(_BUILTIN_PREFIX):
            path = tid[len(_BUILTIN_PREFIX):]
            if path.count(".") >= 2:
                patterns.append(path)
            else:
                patterns.append(f"{path}.*")
            continue
        tool = by_id.get(tid)
        if tool is not None and tool.enabled:
            patterns.append(sift_service.normalize_sift_path(tool.path))
    return sorted(set(patterns))


async def get_sift_for_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    model_config: Any | None = None,
):
    # sem modelo personalizado, ou com SIFT desligada => sem ferramentas
    if model_config is None or not getattr(model_config, "tools_enabled", False):
        return None
    tool_ids = model_config.tool_ids or []
    if not tool_ids:
        return None  # SIFT ligada mas nada marcado => sem ferramentas

    rows = list(await db.scalars(select(Tool).where(Tool.user_id == user_id)))
    allow = _allow_patterns(tool_ids, rows)
    if not allow:
        return None

    # config das ferramentas é POR-MODELO (filter_config.tools), não do perfil global
    tools_cfg = tool_config(model_config)
    tavily = await get_secret(db, user_id, TAVILY_KEY)
    brave = await get_secret(db, user_id, BRAVE_KEY)
    cfg = sift_service.search_config_from_secrets(tavily, brave, tools_cfg.get("web_search"))
    finnhub = await get_secret(db, user_id, FINNHUB_KEY)
    alpha = await get_secret(db, user_id, ALPHAVANTAGE_KEY)
    fin_cfg = sift_service.finance_config_from_secrets(finnhub, alpha, tools_cfg.get("finance"))
    openrouter_key = await get_secret(db, user_id, OPENROUTER_KEY)
    deep_cfg = sift_service.deep_config_from_secrets(openrouter_key, tools_cfg.get("deep_search"))
    # Google (Gmail+Agenda): contas conectadas do usuário filtradas pelas liberadas
    # neste modelo (tools_cfg.google.accounts; vazio = todas). O token NÃO entra na
    # config (é buscado ao vivo na tool) — só id+email das contas + permissões.
    g_prefs = tools_cfg.get("google") or {}
    allowed_ids = {str(x) for x in (g_prefs.get("accounts") or [])}
    g_rows = list(await db.scalars(select(GoogleAccount).where(GoogleAccount.user_id == user_id)))
    g_accounts = [
        {"id": str(a.id), "email": a.email}
        for a in g_rows
        if not allowed_ids or str(a.id) in allowed_ids
    ]
    google_cfg = sift_service.google_config_from_secrets(str(user_id), g_accounts, g_prefs)
    # Tuya/Smart Life: conexão GLOBAL (app_settings) + gating por-modelo. Só busca a
    # conexão se este modelo de fato equipou a tool (evita ler config à toa).
    tuya_cfg = None
    if any(tid == f"{_BUILTIN_PREFIX}smartlife.tuya.devices" for tid in tool_ids):
        from ..integrations import tuya_service
        conn = await tuya_service.get_config(db, str(user_id))
        tuya_cfg = sift_service.tuya_config_from_secrets(conn, tools_cfg.get("tuya"))
    full = await run_in_threadpool(
        sift_service.get_user_sift, str(user_id), rows, cfg, fin_cfg, deep_cfg, google_cfg, tuya_cfg
    )
    if full is None:
        return None
    sift_config = getattr(model_config, "sift_config", None) or {}
    code_mode = bool(getattr(model_config, "code_mode", False))
    try:
        scope = full.scope(allow=allow)
        # Pins: ferramentas "quentes" viram specs de 1a classe (sem discovery).
        # Capturamos os specs pinados de forma SÍNCRONA (atômica no asyncio) e
        # limpamos _pinned na hora — não deixa estado no Sift compartilhado.
        pin_paths = _pinned_paths(sift_config.get("pinned") or [], rows)
        if pin_paths and not code_mode:
            try:
                full._pinned[:] = pin_paths
                scope._aw_openai_tools = scope.openai_tools()  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - path inexistente etc.: segue sem pin
                logger.warning("Falha ao fixar tools SIFT (%s); sem pin", exc)
            finally:
                full._pinned.clear()
        # Modo Código: promove as tools longas a 1ª classe (fora do sandbox).
        if code_mode:
            promote = [p for p in _CODE_MODE_PROMOTE if _allow_match(p, allow)]
            if promote:
                try:
                    full._pinned[:] = promote
                    specs = scope.openai_tools()
                    scope._aw_code_extra_tools = [  # type: ignore[attr-defined]
                        t for t in specs if "__" in ((t.get("function") or {}).get("name") or "")
                    ]
                except Exception as exc:  # noqa: BLE001 - segue sem promoção
                    logger.warning("Falha ao promover tools longas no code mode (%s)", exc)
                finally:
                    full._pinned.clear()
        # metadados p/ o orchestrator: modo de exposição + prompt "quando usar"
        try:
            scope._aw_tools = _tool_catalog(tool_ids, rows)  # type: ignore[attr-defined]
            scope._aw_sift_mode = (sift_config.get("mode") or "prompt")  # type: ignore[attr-defined]
            scope._aw_sift_prompt = (sift_config.get("prompt") or "")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - se o objeto não aceitar atributo, segue sem
            pass
        return scope
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falha ao aplicar scope SIFT (%s); chat sem ferramentas", exc)
        return None
