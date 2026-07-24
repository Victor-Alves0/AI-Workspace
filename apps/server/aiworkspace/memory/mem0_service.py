"""Integração com mem0.

mem0 usa um LLM para extrair/consolidar memórias e um vector store para guardá-las.
Configuramos:
  - LLM      -> OpenRouter (compatível com OpenAI) usando a chave do PRÓPRIO usuário
  - embedder -> local (huggingface/sentence-transformers), sem chave extra
  - vetores  -> o mesmo Postgres + pgvector da aplicação

Como a chave é por usuário, mantemos um cache de instâncias `Memory` por chave.
Toda chamada é bloqueante (mem0 é síncrono) → o orchestrator usa run_in_threadpool.
Se a inicialização falhar, degradamos para no-op (o chat continua funcionando).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from ..config import get_settings

logger = logging.getLogger(__name__)

# embedder local via FastEmbed (ONNX, sem torch, sem chave de API).
# FastEmbed já é dependência da SIFT, então não pesa no ambiente.
_EMBED_MODEL = "BAAI/bge-small-en-v1.5"
_EMBED_DIMS = 384


def _pg_conn_params() -> dict[str, Any]:
    url = urlparse(get_settings().sync_database_url)
    return {
        "dbname": url.path.lstrip("/"),
        "user": url.username,
        "password": url.password,
        "host": url.hostname,
        "port": url.port or 5432,
    }


# --------------------------------------------------------------------------- #
# Desativar/ativar (soft-off): a memória fica no mem0 mas é excluída da
# recuperação por turno. Guardado em `disabled_memories` (migration 0027),
# gerenciado aqui via psycopg2 (mesma conexão que o mem0 usa) — sem plumbing
# pelo run_turn nem model SQLAlchemy.
# --------------------------------------------------------------------------- #
def _flag_ids(user_id: str, status: str | None = None) -> set[str]:
    """Ids na tabela de flags. `status` None = todos (disabled ∪ pending)."""
    import psycopg2
    try:
        conn = psycopg2.connect(**_pg_conn_params())
    except Exception as exc:  # noqa: BLE001
        logger.warning("disabled_memories indisponível: %s", exc)
        return set()
    try:
        with conn, conn.cursor() as cur:
            if status:
                cur.execute(
                    "SELECT memory_id FROM disabled_memories WHERE user_id = %s AND status = %s",
                    (user_id, status),
                )
            else:
                cur.execute("SELECT memory_id FROM disabled_memories WHERE user_id = %s", (user_id,))
            return {r[0] for r in cur.fetchall()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("disabled_memories query falhou: %s", exc)
        return set()
    finally:
        conn.close()


def _disabled_ids(user_id: str) -> set[str]:
    return _flag_ids(user_id, "disabled")


def _pending_ids(user_id: str) -> set[str]:
    return _flag_ids(user_id, "pending")


def _hidden_ids(user_id: str) -> set[str]:
    """Tudo que o modelo NÃO deve ver: desativadas + pendentes de revisão."""
    return _flag_ids(user_id, None)


def _set_flag(user_id: str, memory_ids: list[str], status: str) -> int:
    """Insere flag (status) para ids. Retorna nº afetado."""
    ids = [m for m in (memory_ids or []) if m]
    if not ids:
        return 0
    import psycopg2
    try:
        conn = psycopg2.connect(**_pg_conn_params())
    except Exception as exc:  # noqa: BLE001
        logger.warning("disabled_memories indisponível: %s", exc)
        return 0
    try:
        with conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO disabled_memories (user_id, memory_id, status) VALUES (%s, %s, %s) "
                "ON CONFLICT (user_id, memory_id) DO UPDATE SET status = EXCLUDED.status",
                [(user_id, m, status) for m in ids],
            )
        return len(ids)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_set_flag falhou: %s", exc)
        return 0
    finally:
        conn.close()


def _clear_flag(user_id: str, memory_ids: list[str]) -> int:
    """Remove o flag (reativa / aprova). Retorna nº afetado."""
    ids = [m for m in (memory_ids or []) if m]
    if not ids:
        return 0
    import psycopg2
    try:
        conn = psycopg2.connect(**_pg_conn_params())
        with conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM disabled_memories WHERE user_id = %s AND memory_id = ANY(%s)",
                (user_id, ids),
            )
        conn.close()
        return len(ids)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_clear_flag falhou: %s", exc)
        return 0


def set_disabled(user_id: str, memory_ids: list[str], disabled: bool) -> int:
    """Marca/desmarca memórias como desativadas (soft-off manual)."""
    return _set_flag(user_id, memory_ids, "disabled") if disabled else _clear_flag(user_id, memory_ids)


def set_pending(user_id: str, memory_ids: list[str]) -> int:
    """Marca memórias como PENDENTES de revisão (não usadas até aprovar)."""
    return _set_flag(user_id, memory_ids, "pending")


def _fastembed_embedder():
    """Embeddings locais via FastEmbed embrulhado p/ LangChain.

    O mem0 0.1.x não tem provider "fastembed" nativo; o provider "langchain"
    aceita qualquer instância de Embeddings. FastEmbedEmbeddings usa o mesmo
    modelo ONNX já cacheado no volume (FASTEMBED_CACHE_PATH) pela SIFT.
    """
    from langchain_community.embeddings import FastEmbedEmbeddings

    return FastEmbedEmbeddings(model_name=_EMBED_MODEL)


def _build_config(api_key: str) -> dict[str, Any]:
    s = get_settings()
    pg = _pg_conn_params()
    return {
        "llm": {
            "provider": "openai",
            "config": {
                "model": "openai/gpt-4o-mini",
                "openai_base_url": s.openrouter_base_url,
                "api_key": api_key,
            },
        },
        "embedder": {
            "provider": "langchain",
            "config": {"model": _fastembed_embedder()},
        },
        "vector_store": {
            "provider": "pgvector",
            "config": {
                "collection_name": "aiworkspace_memories",
                "embedding_model_dims": _EMBED_DIMS,
                **pg,
            },
        },
    }


@lru_cache(maxsize=8)
def _memory_for_key(api_key: str):
    """Instância mem0 cacheada por chave de API. Retorna None se indisponível.

    A construção é CARA (carrega o modelo de embedding + conecta no pgvector +
    garante a coleção): ~1-3s na primeira vez por chave. O lru_cache paga isso uma
    vez; `warm()` (no boot) tira esse custo da primeira operação real do usuário."""
    try:
        from mem0 import Memory

        return Memory.from_config(_build_config(api_key))
    except Exception as exc:  # noqa: BLE001
        logger.warning("mem0 indisponível (degradando para no-op): %s", exc)
        return None


def warm(api_key: str) -> bool:
    """Constrói e cacheia o cliente mem0 desta chave — bloqueante (chamar em
    threadpool/boot). Tira o cold-start (~1-3s) da primeira operação real do turno."""
    return _memory_for_key(api_key) is not None


def _scope(run_id: str | None, agent_id: str | None) -> dict[str, str]:
    """Chaves de escopo do mem0. `run_id` isola por chat (sessão); `agent_id`
    (uso futuro) permite isolar por agente/modelo. Só inclui as que vierem."""
    extra: dict[str, str] = {}
    if run_id:
        extra["run_id"] = run_id
    if agent_id:
        extra["agent_id"] = agent_id
    return extra


def search(
    api_key: str,
    query: str,
    user_id: str,
    limit: int = 5,
    *,
    run_id: str | None = None,
    agent_id: str | None = None,
) -> list[str]:
    """Retorna até `limit` memórias relevantes (texto). Bloqueante.

    Escopo: sempre por `user_id`; e por `run_id` (chat) quando informado —
    hoje é obrigatório pela rota, então memórias não vazam entre chats."""
    mem = _memory_for_key(api_key)
    if mem is None:
        return []
    try:
        res = mem.search(query=query, user_id=user_id, limit=limit, **_scope(run_id, agent_id))
        items = res.get("results", res) if isinstance(res, dict) else res
        return [i["memory"] for i in items if isinstance(i, dict) and i.get("memory")]
    except Exception as exc:  # noqa: BLE001
        logger.warning("mem0.search falhou: %s", exc)
        return []


def add(
    api_key: str,
    messages: list[dict[str, str]],
    user_id: str,
    *,
    run_id: str | None = None,
    agent_id: str | None = None,
) -> dict | None:
    """Persiste o par de mensagens como memória. Bloqueante. Devolve o resultado
    do mem0 ({results:[{id,event}]}) para quem precisar dos ids novos."""
    mem = _memory_for_key(api_key)
    if mem is None:
        return None
    try:
        return mem.add(messages, user_id=user_id, **_scope(run_id, agent_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("mem0.add falhou: %s", exc)
        return None


def _new_ids(res: dict | None) -> list[str]:
    """Ids ADD/UPDATE de um retorno do mem0.add."""
    if not res:
        return []
    items = res.get("results", res) if isinstance(res, dict) else res
    return [
        str(i["id"]) for i in items
        if isinstance(i, dict) and i.get("id") and i.get("event") in ("ADD", "UPDATE")
    ]


# --------------------------------------------------------------------------- #
# Escopos (global / model / chat) e gerência (listar/editar/excluir)
#
# Três "tiers" mapeados nos eixos do mem0:
#   global -> só user_id (sem run_id/agent_id)  ·  compartilhado por tudo
#   model  -> agent_id  (modelo/agente)         ·  compartilhado por chats do modelo
#   chat   -> run_id    (a conversa)            ·  isolado num chat
# Leitura por turno = UNIÃO dos escopos ligados (nunca vaza de outros chats/modelos).
# Escrita por turno = UM escopo (o que o usuário escolheu para o chat).
# --------------------------------------------------------------------------- #
def _field(item: dict, key: str) -> str | None:
    """Lê `run_id`/`agent_id` de um item do mem0 (topo ou dentro de `metadata`)."""
    v = item.get(key)
    if v:
        return str(v)
    md = item.get("metadata") or {}
    return str(md[key]) if md.get(key) else None


# Bancos de memória: coleções nomeadas COMPARTILHÁVEIS entre modelos. Vivem no
# eixo agent_id do mem0 com este prefixo, para não colidir com o escopo "model"
# (cujo agent_id é o id do ModelConfig ou "base:<modelo>").
_BANK_PREFIX = "bank:"
# Projeto: memória compartilhada por TODOS os chats de uma pasta (o "projeto").
# Também mora no eixo agent_id, com prefixo próprio; o id é o da pasta (folder_id).
_PROJECT_PREFIX = "project:"


def _item_scope(item: dict) -> str:
    if _field(item, "run_id"):
        return "chat"
    aid = _field(item, "agent_id")
    if aid:
        if aid.startswith(_BANK_PREFIX):
            return "bank"
        if aid.startswith(_PROJECT_PREFIX):
            return "project"
        return "model"
    return "global"


def _normalize(item: dict) -> dict[str, Any]:
    aid = _field(item, "agent_id")
    scope = _item_scope(item)
    return {
        "id": item.get("id"),
        "text": item.get("memory") or item.get("data") or "",
        "scope": scope,
        # no escopo "model" model_id é o agent_id; nos demais expomos o id específico
        "model_id": aid if scope == "model" else None,
        "bank_id": aid[len(_BANK_PREFIX):] if scope == "bank" and aid else None,
        "project_id": aid[len(_PROJECT_PREFIX):] if scope == "project" and aid else None,
        "chat_id": _field(item, "run_id"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "disabled": False,
        "pending": False,
    }


def search_for_turn(
    api_key: str,
    query: str,
    user_id: str,
    *,
    chat_id: str | None,
    agent_id: str | None,
    read: dict[str, bool],
    banks: list[str] | None = None,
    project_id: str | None = None,
    limit: int = 6,
) -> list[dict[str, str]]:
    """Memórias relevantes para ESTE turno = união dos escopos ligados em `read`
    ({global, model, chat, project}) MAIS os bancos acoplados (`banks`, ids). Uma única
    busca por user_id (rankeada por similaridade), particionada no cliente: mantém
    globais, as do modelo atual (agent_id), as deste chat (run_id), as do projeto
    (pasta) e as dos bancos acoplados — nunca de outros chats/modelos/projetos/bancos.
    Desativadas/pendentes são puladas. Retorna [{id, text, scope}]. Bloqueante."""
    bank_aids = {_BANK_PREFIX + b for b in (banks or [])}
    project_aid = _PROJECT_PREFIX + project_id if project_id else None
    read_project = bool(read.get("project")) and project_aid is not None
    if not any(read.values()) and not bank_aids:
        return []
    mem = _memory_for_key(api_key)
    if mem is None:
        return []
    try:
        res = mem.search(query=query, user_id=user_id, limit=max(limit * 4, 20))
        items = res.get("results", res) if isinstance(res, dict) else res
    except Exception as exc:  # noqa: BLE001
        logger.warning("mem0.search falhou: %s", exc)
        return []
    off = _hidden_ids(user_id)  # desativadas + pendentes: o modelo não as vê
    out: list[dict[str, str]] = []
    for it in items:
        if not isinstance(it, dict) or not it.get("memory"):
            continue
        if str(it.get("id")) in off:
            continue
        sc = _item_scope(it)
        keep = (
            (sc == "global" and read.get("global"))
            or (sc == "chat" and read.get("chat") and _field(it, "run_id") == chat_id)
            or (sc == "model" and read.get("model") and _field(it, "agent_id") == agent_id)
            or (sc == "project" and read_project and _field(it, "agent_id") == project_aid)
            or (sc == "bank" and _field(it, "agent_id") in bank_aids)
        )
        if keep:
            out.append({"id": str(it.get("id")), "text": it["memory"], "scope": sc})
        if len(out) >= limit:
            break
    return out


def add_scoped(
    api_key: str,
    messages: list[dict[str, str]],
    user_id: str,
    *,
    scope: str,
    chat_id: str | None = None,
    agent_id: str | None = None,
    project_id: str | None = None,
    review: bool = False,
) -> None:
    """Grava a memória pós-turno no escopo escolhido pelo chat: global / model /
    chat / project (pasta) / "bank:<id>" (banco compartilhado). `off` (ou escopo sem
    o id necessário) = não grava. Com `review`, as novas memórias entram como
    PENDENTES (o modelo não as usa até serem aprovadas)."""
    if scope == "off":
        return
    run_id = chat_id if scope == "chat" else None
    aid = agent_id if scope == "model" else None
    if scope == "project":  # escrita no projeto: agent_id = "project:<folder_id>"
        aid = _PROJECT_PREFIX + project_id if project_id else None
    if scope.startswith(_BANK_PREFIX):  # escrita num banco: agent_id = "bank:<id>"
        aid = scope
    if scope == "chat" and not run_id:
        return
    if scope == "model" and not aid:
        return
    if scope == "project" and not aid:
        return
    res = add(api_key, messages, user_id, run_id=run_id, agent_id=aid)
    if review:
        new = _new_ids(res)
        if new:
            set_pending(user_id, new)


def list_memories(
    api_key: str,
    user_id: str,
    *,
    scope: str | None = None,
    chat_id: str | None = None,
    agent_id: str | None = None,
    bank_id: str | None = None,
    project_id: str | None = None,
    query: str = "",
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Lista memórias (normalizadas) do usuário, filtráveis por escopo/chat/modelo/
    banco. Com `query` faz busca semântica; senão lista tudo. Bloqueante."""
    mem = _memory_for_key(api_key)
    if mem is None:
        return []
    try:
        if query.strip():
            res = mem.search(query=query, user_id=user_id, limit=limit)
        else:
            res = mem.get_all(user_id=user_id, limit=limit)
        items = res.get("results", res) if isinstance(res, dict) else res
    except Exception as exc:  # noqa: BLE001
        logger.warning("mem0.list falhou: %s", exc)
        return []
    rows = [_normalize(it) for it in items if isinstance(it, dict)]
    if scope:
        rows = [r for r in rows if r["scope"] == scope]
    if chat_id:
        rows = [r for r in rows if r["chat_id"] == chat_id]
    if agent_id:
        rows = [r for r in rows if r["model_id"] == agent_id]
    if bank_id:
        rows = [r for r in rows if r.get("bank_id") == bank_id]
    if project_id:
        rows = [r for r in rows if r.get("project_id") == project_id]
    dis = _disabled_ids(user_id)
    pend = _pending_ids(user_id)
    for r in rows:
        rid = str(r["id"])
        r["disabled"] = rid in dis
        r["pending"] = rid in pend
    return rows


def add_manual(
    api_key: str,
    user_id: str,
    text: str,
    *,
    scope: str,
    chat_id: str | None = None,
    agent_id: str | None = None,
    pending: bool = False,
) -> bool:
    """Adiciona uma memória CRUA (sem extração via LLM, `infer=False`) num escopo:
    global / model / chat / bank (agent_id = "bank:<id>"). Com `pending`, entra
    PENDENTE (aguardando aprovação) — usado pela curadoria do Aprendizado Proativo."""
    mem = _memory_for_key(api_key)
    if mem is None or not text.strip():
        return False
    run_id = chat_id if scope == "chat" else None
    aid = agent_id if scope == "model" else None
    if scope == "bank" and agent_id:
        aid = agent_id if agent_id.startswith(_BANK_PREFIX) else _BANK_PREFIX + agent_id
    if scope == "project" and agent_id:
        aid = agent_id if agent_id.startswith(_PROJECT_PREFIX) else _PROJECT_PREFIX + agent_id
    try:
        res = mem.add([{"role": "user", "content": text.strip()}], user_id=user_id,
                      infer=False, **_scope(run_id, aid))
        if pending:
            new = _new_ids(res)
            if new:
                set_pending(user_id, new)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("mem0.add_manual falhou: %s", exc)
        return False


def update_memory(api_key: str, memory_id: str, text: str) -> bool:
    mem = _memory_for_key(api_key)
    if mem is None:
        return False
    try:
        mem.update(memory_id, text)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("mem0.update falhou: %s", exc)
        return False


def delete_memory(api_key: str, memory_id: str) -> bool:
    mem = _memory_for_key(api_key)
    if mem is None:
        return False
    try:
        mem.delete(memory_id)
        _forget_disabled(memory_id)  # limpa a flag (se houver)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("mem0.delete falhou: %s", exc)
        return False


def _forget_disabled(memory_id: str) -> None:
    import psycopg2
    try:
        conn = psycopg2.connect(**_pg_conn_params())
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM disabled_memories WHERE memory_id = %s", (memory_id,))
        conn.close()
    except Exception:  # noqa: BLE001 - limpeza best-effort
        pass


def delete_scope(
    api_key: str,
    user_id: str,
    *,
    scope: str,
    chat_id: str | None = None,
    agent_id: str | None = None,
) -> int:
    """Apaga TODAS as memórias de um escopo. Para `global`, apaga item a item as
    globais (delete_all(user_id) apagaria TUDO). Retorna nº apagado (-1 = em lote)."""
    mem = _memory_for_key(api_key)
    if mem is None:
        return 0
    try:
        if scope == "chat" and chat_id:
            mem.delete_all(user_id=user_id, run_id=chat_id)
            return -1
        if scope == "model" and agent_id:
            mem.delete_all(user_id=user_id, agent_id=agent_id)
            return -1
        if scope == "bank" and agent_id:  # agent_id = "bank:<id>"
            mem.delete_all(user_id=user_id, agent_id=agent_id)
            return -1
        if scope == "project" and agent_id:  # agent_id = "project:<folder_id>"
            mem.delete_all(user_id=user_id, agent_id=agent_id)
            return -1
        if scope == "global":
            n = 0
            for it in list_memories(api_key, user_id, scope="global", limit=2000):
                if it["id"] and delete_memory(api_key, it["id"]):
                    n += 1
            return n
    except Exception as exc:  # noqa: BLE001
        logger.warning("mem0.delete_scope falhou: %s", exc)
    return 0


def scope_summary(api_key: str, user_id: str) -> dict[str, Any]:
    """Contagens por escopo + ids de modelos/chats que têm memória (para os
    seletores da UI). Os nomes são resolvidos na rota (app DB)."""
    items = list_memories(api_key, user_id, limit=2000)
    models: dict[str, int] = {}
    chats: dict[str, int] = {}
    banks: dict[str, int] = {}
    projects: dict[str, int] = {}
    glob = 0
    for i in items:
        if i["scope"] == "global":
            glob += 1
        elif i["scope"] == "model" and i["model_id"]:
            models[i["model_id"]] = models.get(i["model_id"], 0) + 1
        elif i["scope"] == "chat" and i["chat_id"]:
            chats[i["chat_id"]] = chats.get(i["chat_id"], 0) + 1
        elif i["scope"] == "bank" and i.get("bank_id"):
            banks[i["bank_id"]] = banks.get(i["bank_id"], 0) + 1
        elif i["scope"] == "project" and i.get("project_id"):
            projects[i["project_id"]] = projects.get(i["project_id"], 0) + 1
    return {"global": glob, "models": models, "chats": chats, "banks": banks,
            "projects": projects, "total": len(items)}


def bank_counts(api_key: str, user_id: str) -> dict[str, int]:
    """Nº de memórias por banco (id do banco → contagem)."""
    return scope_summary(api_key, user_id).get("banks", {})
