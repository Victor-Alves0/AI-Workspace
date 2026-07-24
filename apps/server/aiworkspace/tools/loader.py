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

from ..config import get_settings
from ..models import GithubAccount, GoogleAccount, Tool, User
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
# media.video.transcribe entra aqui pelo mesmo motivo (baixar áudio + STT leva minutos).
_CODE_MODE_PROMOTE = ("research.deep.run", "media.video.transcribe")

# Codespace: num chat VINCULADO a um projeto, as tools de LEITURA de código são
# liberadas mesmo sem estarem marcadas no modelo — vincular o chat ao projeto já
# é o consentimento explícito de ler aquele código, e sem isso o chat de projeto
# fica inútil (foi o que aconteceu: um modelo sem elas equipadas abria o chat do
# projeto sem conseguir navegar nada). A de ESCRITA continua OPT-IN por-modelo:
# cria/apaga arquivos e faz push, então exige permissão explícita como as demais
# ações destrutivas.
_CODESPACE_READ = ("code.graph.query", "code.files.browse", "code.flow.analyze")
_CODESPACE_WRITE = "code.files.write"
_CODESPACE_ALL = (*_CODESPACE_READ, _CODESPACE_WRITE)


def _allow_match(path: str, allow: list[str]) -> bool:
    return any(path == a or (a.endswith(".*") and path.startswith(a[:-1])) for a in allow)


def codespace_allow(allow: list[str]) -> list[str]:
    """Escopo de um chat de projeto: o do modelo + as tools de LEITURA de código."""
    return sorted(set(allow) | set(_CODESPACE_READ))


def codespace_pins(pin_paths: list[str], allow: list[str]) -> list[str]:
    """Pins de um chat de projeto: os do modelo + as tools de código QUE ESTÃO no
    escopo (a de escrita só entra se o modelo a equipou — nunca é auto-liberada)."""
    return sorted(set(pin_paths) | {p for p in _CODESPACE_ALL if _allow_match(p, allow)})


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


async def _assemble_configs(db: AsyncSession, user_id: uuid.UUID, model_config: Any | None, tool_ids: list):
    """Monta as configs das ferramentas de sistema (busca web, finanças, deep search,
    Google, Tuya) a partir dos segredos + prefs POR-MODELO (filter_config.tools) e do
    perfil global. Compartilhado por `get_sift_for_user` (scoped) e
    `build_full_sift_for_user` (completo, p/ o Debug de Tools)."""
    tools_cfg = tool_config(model_config)
    tavily = await get_secret(db, user_id, TAVILY_KEY)
    brave = await get_secret(db, user_id, BRAVE_KEY)
    # pesquisa na web em camadas: env < Conexões → Web (conta) < config do modelo
    u = await db.get(User, user_id)
    prof = (u.profile or {}) if u else {}
    # confirmação antes de escritas sensíveis (e-mail/agenda/casa) é OPT-IN global
    # (Configurações → Segurança). Padrão: desligado → a IA executa direto.
    confirm_actions = bool((prof.get("security") or {}).get("confirm_actions", False))
    user_ws = (prof.get("web_search") or {})
    model_ws = tools_cfg.get("web_search") or {}
    web_prefs = {**user_ws, **model_ws} or None
    cfg = sift_service.search_config_from_secrets(tavily, brave, web_prefs)
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
    google_cfg = sift_service.google_config_from_secrets(
        str(user_id), g_accounts, g_prefs, confirm_actions=confirm_actions
    )
    # GitHub: contas conectadas do usuário (PAT/OAuth) filtradas pelas liberadas neste
    # modelo (tools_cfg.github.accounts; vazio = todas). O token NÃO entra na config
    # (é buscado ao vivo na tool) — só id+login das contas + ops.
    gh_prefs = tools_cfg.get("github") or {}
    gh_allowed_ids = {str(x) for x in (gh_prefs.get("accounts") or [])}
    gh_rows = list(await db.scalars(select(GithubAccount).where(GithubAccount.user_id == user_id)))
    gh_accounts = [
        {"id": str(a.id), "login": a.login}
        for a in gh_rows
        if not gh_allowed_ids or str(a.id) in gh_allowed_ids
    ]
    github_cfg = sift_service.github_config_from_secrets(
        str(user_id), gh_accounts, gh_prefs, confirm_actions=confirm_actions
    )
    # Tuya/Smart Life: conexão GLOBAL (app_settings) + gating por-modelo. Só busca a
    # conexão se este modelo de fato equipou a tool (evita ler config à toa).
    tuya_cfg = None
    if any(tid == f"{_BUILTIN_PREFIX}smartlife.tuya.devices" for tid in tool_ids):
        from ..integrations import tuya_service
        conn = await tuya_service.get_config(db, str(user_id))
        tuya_cfg = sift_service.tuya_config_from_secrets(
            conn, tools_cfg.get("tuya"), confirm_actions=confirm_actions
        )
    # Mensagens (WhatsApp/Telegram/Discord): conexões de chat ATIVAS do usuário
    # filtradas pelas liberadas neste modelo (tools_cfg.messaging.accounts; vazio =
    # todas). Nenhum token entra na config — resolvido ao vivo por conexão na tool.
    # Só busca as conexões se o modelo de fato equipou a tool (evita I/O à toa).
    messaging_cfg = None
    if any(tid == f"{_BUILTIN_PREFIX}messaging.chat.manage" for tid in tool_ids):
        from ..integrations import messaging_service
        m_prefs = tools_cfg.get("messaging") or {}
        m_allowed_ids = {str(x) for x in (m_prefs.get("accounts") or [])}
        m_accounts = [
            a for a in await messaging_service.gather_accounts(db, user_id)
            if not m_allowed_ids or a["id"] in m_allowed_ids
        ]
        messaging_cfg = sift_service.messaging_config_from_secrets(
            str(user_id), m_accounts, m_prefs, confirm_actions=confirm_actions
        )
    # Navegador headless: config POR-USUÁRIO (Conexões → Web). ws_url/token/enabled
    # ficam no profile.browser; o endpoint efetivo (com fallback ao env) é resolvido
    # na tool. Só um dict simples — sem segredo de terceiros.
    browser_cfg = dict(prof.get("browser") or {})
    # Higgsfield: conexão por-usuário (app_settings) — mesmo padrão da Tuya: só
    # busca se este modelo de fato equipou a tool (evita ler config à toa).
    higgsfield_cfg = None
    if any(tid == f"{_BUILTIN_PREFIX}higgsfield.media.generate" for tid in tool_ids):
        from ..integrations import higgsfield_service
        hf_conn = await higgsfield_service.get_config(db, str(user_id))
        higgsfield_cfg = sift_service.higgsfield_config_from_secrets(hf_conn)
    return cfg, fin_cfg, deep_cfg, google_cfg, tuya_cfg, github_cfg, messaging_cfg, browser_cfg, higgsfield_cfg


async def build_full_sift_for_user(db: AsyncSession, user_id: uuid.UUID):
    """Instância SIFT COMPLETA e SEM escopo do usuário (builtins + tools dele), para o
    Debug de Tools chamar qualquer ferramenta direto (`sift.execute_tool(path, params)`).
    Usa as configs globais do usuário (sem gating por-modelo). None se a SIFT falhar."""
    rows = list(await db.scalars(select(Tool).where(Tool.user_id == user_id)))
    cfg, fin_cfg, deep_cfg, google_cfg, tuya_cfg, github_cfg, messaging_cfg, browser_cfg, higgsfield_cfg = await _assemble_configs(db, user_id, None, [])
    return await run_in_threadpool(
        sift_service.get_user_sift, str(user_id), rows, cfg, fin_cfg, deep_cfg, google_cfg, tuya_cfg, github_cfg, messaging_cfg, browser_cfg, higgsfield_cfg
    )


async def get_sift_for_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    model_config: Any | None = None,
    codespace_project_id: str | None = None,
):
    """`codespace_project_id`: chat vinculado a um projeto do Codespace — libera as
    tools de leitura de código (ver `_CODESPACE_READ`) e FIXA as de código que
    estiverem no escopo, para o modelo vê-las direto em vez de depender do
    discovery (`search_tools`)."""
    # sem modelo personalizado, ou com SIFT desligada => sem ferramentas
    # (o mestre `tools_enabled` manda mesmo num chat de projeto — se o usuário
    # desligou as ferramentas do modelo, o Codespace não passa por cima)
    if model_config is None or not getattr(model_config, "tools_enabled", False):
        return None
    tool_ids = model_config.tool_ids or []
    in_codespace = bool(codespace_project_id)
    if not tool_ids and not in_codespace:
        return None  # SIFT ligada mas nada marcado => sem ferramentas

    rows = list(await db.scalars(select(Tool).where(Tool.user_id == user_id)))
    allow = _allow_patterns(tool_ids, rows)
    if in_codespace:
        allow = codespace_allow(allow)
    if not allow:
        return None

    cfg, fin_cfg, deep_cfg, google_cfg, tuya_cfg, github_cfg, messaging_cfg, browser_cfg, higgsfield_cfg = await _assemble_configs(
        db, user_id, model_config, tool_ids
    )
    full = await run_in_threadpool(
        sift_service.get_user_sift, str(user_id), rows, cfg, fin_cfg, deep_cfg, google_cfg, tuya_cfg, github_cfg, messaging_cfg, browser_cfg, higgsfield_cfg
    )
    if full is None:
        return None
    sift_config = getattr(model_config, "sift_config", None) or {}
    # mesmo critério do chat (_code_mode): o flag do modelo SÓ vale com o off-switch
    # global ligado — senão o turno roda em modo normal e os PINS devem valer.
    code_mode = bool(getattr(model_config, "code_mode", False)) and get_settings().allow_code_mode
    try:
        # Pins POR-ESCOPO (SIFT >= 0.7): ferramentas "quentes" viram specs de 1ª
        # classe (sem discovery) direto no scope — sem mutar estado do Sift
        # compartilhado (substituiu o antigo hack de _pinned set/clear). No modo
        # código, o MESMO mecanismo promove as tools longas (rodam fora do sandbox
        # do run_code — o watchdog mataria e descartaria o resultado).
        if code_mode:
            pin_paths = [p for p in _CODE_MODE_PROMOTE if _allow_match(p, allow)]
        else:
            pin_paths = _pinned_paths(sift_config.get("pinned") or [], rows)
        if in_codespace:
            # num chat de projeto as tools de código são as "quentes" por
            # definição: entram como specs de 1ª classe (sem discovery), senão o
            # modelo precisa adivinhar que elas existem via search_tools — o
            # caminho onde modelos fracos desistem e respondem "não tenho acesso
            # ao código" (ver [[tool-exposure-hallucination]]).
            pin_paths = codespace_pins(pin_paths, allow)
        try:
            scope = full.scope(allow=allow, pin=pin_paths or None)
        except Exception as exc:  # noqa: BLE001 - pin fora do allow etc.: segue sem pin
            logger.warning("Falha ao fixar tools SIFT (%s); escopo sem pin", exc)
            scope = full.scope(allow=allow)
            pin_paths = []
        # Modo Código: os specs pinados (nome flat com "__") entram como tools de
        # 1ª classe AO LADO do run_code (code_tools() não inclui pins).
        if code_mode and pin_paths:
            try:
                scope.meta["code_extra_tools"] = [
                    t for t in scope.openai_tools()
                    if "__" in ((t.get("function") or {}).get("name") or "")
                ]
            except Exception as exc:  # noqa: BLE001 - segue sem promoção
                logger.warning("Falha ao promover tools longas no code mode (%s)", exc)
        # metadados p/ o orchestrator (scope.meta, oficial na SIFT >= 0.7 —
        # substituiu os antigos atributos injetados _aw_*): catálogo legível,
        # modo de exposição e prompt "quando usar"
        scope.meta["catalog"] = _tool_catalog(tool_ids, rows)
        scope.meta["sift_mode"] = sift_config.get("mode") or "prompt"
        scope.meta["sift_prompt"] = sift_config.get("prompt") or ""
        return scope
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falha ao aplicar scope SIFT (%s); chat sem ferramentas", exc)
        return None
