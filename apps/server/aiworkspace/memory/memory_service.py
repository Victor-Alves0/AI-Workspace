"""Memória de longo prazo do usuário, nativa sobre o pgvector.

Substitui o mem0 (e o LangChain que ele exigia). Guarda na MESMA tabela e no MESMO
formato que o mem0 usava — `aiworkspace_memories (id uuid, vector vector(384), payload
jsonb)`, payload com data/hash/user_id/run_id/agent_id/created_at/updated_at — e com o
mesmo modelo de embedding (`knowledge/embeddings`), então as memórias existentes
continuam valendo sem migração de dados.

  - vetores   -> Postgres + pgvector da aplicação (asyncpg via pgsync, síncrono)
  - embedding -> FastEmbed local (sem chave)
  - extração  -> LLM pelo OpenRouter com a chave do PRÓPRIO usuário (só em `add`)

Toda função é bloqueante → os chamadores usam run_in_threadpool. Falhas degradam para
"sem memória" (o chat continua funcionando) e ficam registradas no health.

Escopos, nos eixos herdados do mem0:
  global  -> só user_id                    · compartilhado por tudo
  model   -> agent_id = id do modelo       · compartilhado pelos chats do modelo
  chat    -> run_id = id do chat           · isolado na conversa
  project -> agent_id = "project:<pasta>"  · chats de uma pasta
  bank    -> agent_id = "bank:<id>"        · banco nomeado, acoplável a modelos
Leitura por turno = UNIÃO dos escopos ligados; escrita = UM escopo.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from ..config import get_settings

logger = logging.getLogger(__name__)

_TABLE = "aiworkspace_memories"
# modelo da extração (barato e bom em JSON); mesmo que o mem0 usava
_LLM_MODEL = "openai/gpt-4o-mini"
_LLM_TIMEOUT = 60.0
# memórias parecidas consultadas por fato novo, na hora de decidir ADD/UPDATE/DELETE
_SIMILAR_PER_FACT = 5

_BANK_PREFIX = "bank:"
_PROJECT_PREFIX = "project:"


# --------------------------------------------------------------------------- #
# Conexão
# --------------------------------------------------------------------------- #
def _run(sql: str, params: Any = None, *, fetch: bool = True) -> list[Any]:
    """Executa uma instrução numa conexão própria. SQL no estilo `%s` (convertido para
    o asyncpg por `pgsync.from_pyformat`). Linhas = asyncpg.Record (índice ou nome)."""
    from .. import pgsync

    q = pgsync.from_pyformat(sql)
    args = tuple(params or ())
    if fetch:
        return pgsync.fetch(q, *args)
    pgsync.execute(q, *args)
    return []


def _exec(sql: str, params: Any = None) -> int:
    """Executa e devolve o nº de linhas afetadas."""
    from .. import pgsync

    return pgsync.execute(pgsync.from_pyformat(sql), *tuple(params or ()))


def _vec(text: str) -> str:
    from ..knowledge.embeddings import embed_query, to_pgvector

    # o mem0 usava embed_query para TUDO (gravar e buscar); manter isso deixa os
    # vetores novos comparáveis aos já gravados
    return to_pgvector(embed_query(text))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _valid_id(memory_id: str) -> bool:
    try:
        uuid.UUID(str(memory_id))
        return True
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------- #
# Desativar/ativar e revisão: `disabled_memories` (status disabled | pending).
# O modelo não vê nenhuma das duas.
# --------------------------------------------------------------------------- #
def _flag_ids(user_id: str, status: str | None = None) -> set[str]:
    """Ids na tabela de flags. `status` None = todos (disabled ∪ pending)."""
    try:
        if status:
            rows = _run(
                "SELECT memory_id FROM disabled_memories WHERE user_id::text = %s AND status = %s",
                (str(user_id), status),
            )
        else:
            rows = _run("SELECT memory_id FROM disabled_memories WHERE user_id::text = %s", (str(user_id),))
        return {r[0] for r in rows}
    except Exception as exc:  # noqa: BLE001
        logger.warning("disabled_memories indisponível: %s", exc)
        return set()


def _set_flag(user_id: str, memory_ids: list[str], status: str) -> int:
    ids = [m for m in (memory_ids or []) if m]
    if not ids:
        return 0
    try:
        from .. import pgsync

        pgsync.executemany(
            pgsync.from_pyformat(
                "INSERT INTO disabled_memories (user_id, memory_id, status) "
                "VALUES (%s::text::uuid, %s, %s) "
                "ON CONFLICT (user_id, memory_id) DO UPDATE SET status = EXCLUDED.status"),
            [(str(user_id), m, status) for m in ids],
        )
        return len(ids)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_set_flag falhou: %s", exc)
        return 0


def _clear_flag(user_id: str, memory_ids: list[str]) -> int:
    ids = [m for m in (memory_ids or []) if m]
    if not ids:
        return 0
    try:
        _run("DELETE FROM disabled_memories WHERE user_id::text = %s AND memory_id = ANY(%s::text[])",
             (str(user_id), ids), fetch=False)
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


# --------------------------------------------------------------------------- #
# Saúde
# --------------------------------------------------------------------------- #
def warm(api_key: str = "") -> bool:
    """Carrega o modelo de embedding e confere a tabela — tira o cold-start (~1-3s)
    da primeira operação real. Bloqueante. `api_key` é ignorado (o LLM só é usado
    na extração); mantido por compatibilidade com os chamadores."""
    try:
        _vec("warm")
        _run(f"SELECT 1 FROM {_TABLE} LIMIT 1")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("memória indisponível: %s", exc)
        try:
            from ..health_service import record as _health_record
            _health_record("memory", "no_op", severity="degraded", detail={"error": str(exc)[:400]})
        except Exception:  # noqa: BLE001 - nunca deixar a observação derrubar o observado
            pass
        return False


# --------------------------------------------------------------------------- #
# Escopo ↔ payload / SQL
# --------------------------------------------------------------------------- #
def _item_scope(payload: dict) -> str:
    if payload.get("run_id"):
        return "chat"
    aid = payload.get("agent_id") or ""
    if aid.startswith(_BANK_PREFIX):
        return "bank"
    if aid.startswith(_PROJECT_PREFIX):
        return "project"
    return "model" if aid else "global"


def _normalize(mem_id: Any, payload: dict) -> dict[str, Any]:
    aid = payload.get("agent_id")
    scope = _item_scope(payload)
    return {
        "id": str(mem_id),
        "text": payload.get("data") or "",
        "scope": scope,
        "model_id": aid if scope == "model" else None,
        "bank_id": aid[len(_BANK_PREFIX):] if scope == "bank" else None,
        "project_id": aid[len(_PROJECT_PREFIX):] if scope == "project" else None,
        "chat_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "disabled": False,
        "pending": False,
    }


_RUN = "payload->>'run_id'"
_AGENT = "payload->>'agent_id'"


def _exact_scope_sql(run_id: str | None, agent_id: str | None) -> tuple[str, list]:
    """Condição do escopo EXATO de escrita (a deduplicação só compara dentro dele)."""
    if run_id:
        return f"{_RUN} = %s", [run_id]
    if agent_id:
        return f"{_RUN} IS NULL AND {_AGENT} = %s", [agent_id]
    return f"{_RUN} IS NULL AND {_AGENT} IS NULL", []


def _list_filter_sql(
    scope: str | None, chat_id: str | None, agent_id: str | None,
    bank_id: str | None, project_id: str | None,
) -> tuple[list[str], list]:
    cond: list[str] = []
    params: list = []
    if scope == "global":
        cond.append(f"{_RUN} IS NULL AND {_AGENT} IS NULL")
    elif scope == "chat":
        cond.append(f"{_RUN} IS NOT NULL")
    elif scope == "bank":
        cond.append(f"{_RUN} IS NULL AND {_AGENT} LIKE 'bank:%%'")
    elif scope == "project":
        cond.append(f"{_RUN} IS NULL AND {_AGENT} LIKE 'project:%%'")
    elif scope == "model":
        cond.append(f"{_RUN} IS NULL AND {_AGENT} IS NOT NULL "
                    f"AND {_AGENT} NOT LIKE 'bank:%%' AND {_AGENT} NOT LIKE 'project:%%'")
    if chat_id:
        cond.append(f"{_RUN} = %s")
        params.append(chat_id)
    if agent_id:  # id de MODELO (escopo model)
        cond.append(f"{_RUN} IS NULL AND {_AGENT} = %s")
        params.append(agent_id)
    if bank_id:
        cond.append(f"{_RUN} IS NULL AND {_AGENT} = %s")
        params.append(_BANK_PREFIX + bank_id)
    if project_id:
        cond.append(f"{_RUN} IS NULL AND {_AGENT} = %s")
        params.append(_PROJECT_PREFIX + project_id)
    return cond, params


# --------------------------------------------------------------------------- #
# Leitura
# --------------------------------------------------------------------------- #
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
    ({global, model, chat, project}) MAIS os bancos acoplados (`banks`, ids) — nunca de
    outros chats/modelos/projetos/bancos. Desativadas/pendentes ficam de fora. O filtro
    é feito NA busca (antes o mem0 trazia 4× o limite e filtrava depois, perdendo as
    relevantes quando o usuário tinha muitas memórias de outros escopos).
    Retorna [{id, text, scope}]. Bloqueante."""
    ors: list[str] = []
    params: list = []
    if read.get("global"):
        ors.append(f"({_RUN} IS NULL AND {_AGENT} IS NULL)")
    if read.get("chat") and chat_id:
        ors.append(f"{_RUN} = %s")
        params.append(chat_id)
    if read.get("model") and agent_id:
        ors.append(f"({_RUN} IS NULL AND {_AGENT} = %s)")
        params.append(agent_id)
    if read.get("project") and project_id:
        ors.append(f"({_RUN} IS NULL AND {_AGENT} = %s)")
        params.append(_PROJECT_PREFIX + project_id)
    bank_aids = [_BANK_PREFIX + b for b in (banks or []) if b]
    if bank_aids:
        ors.append(f"({_RUN} IS NULL AND {_AGENT} = ANY(%s::text[]))")
        params.append(bank_aids)
    if not ors:
        return []
    try:
        rows = _run(
            f"""SELECT id, payload FROM {_TABLE} t
                WHERE payload->>'user_id' = %s AND ({' OR '.join(ors)})
                  AND NOT EXISTS (SELECT 1 FROM disabled_memories d
                                  WHERE d.user_id::text = %s AND d.memory_id = t.id::text)
                ORDER BY vector <=> %s::text::vector LIMIT %s""",
            [user_id, *params, user_id, _vec(query), limit],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("busca de memória falhou: %s", exc)
        return []
    return [{"id": str(i), "text": p.get("data") or "", "scope": _item_scope(p)}
            for i, p in rows if p.get("data")]


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
    banco/projeto. Com `query`, ordena por semelhança; senão, as mais recentes antes."""
    cond, params = _list_filter_sql(scope, chat_id, agent_id, bank_id, project_id)
    where = " AND ".join(["payload->>'user_id' = %s", *cond])
    try:
        if query.strip():
            rows = _run(f"SELECT id, payload FROM {_TABLE} WHERE {where} "
                        f"ORDER BY vector <=> %s::text::vector LIMIT %s",
                        [user_id, *params, _vec(query), limit])
        else:
            rows = _run(f"SELECT id, payload FROM {_TABLE} WHERE {where} "
                        f"ORDER BY COALESCE(payload->>'updated_at', payload->>'created_at') DESC "
                        f"NULLS LAST LIMIT %s",
                        [user_id, *params, limit])
    except Exception as exc:  # noqa: BLE001
        logger.warning("listagem de memória falhou: %s", exc)
        return []
    out = [_normalize(i, p) for i, p in rows]
    dis = _flag_ids(user_id, "disabled")
    pend = _flag_ids(user_id, "pending")
    for r in out:
        r["disabled"] = r["id"] in dis
        r["pending"] = r["id"] in pend
    return out


def scope_summary(api_key: str, user_id: str) -> dict[str, Any]:
    """Contagens por escopo + ids de modelos/chats/bancos/projetos que têm memória
    (para os seletores da UI). Os nomes são resolvidos na rota."""
    out: dict[str, Any] = {"global": 0, "models": {}, "chats": {}, "banks": {},
                           "projects": {}, "total": 0}
    try:
        rows = _run(f"SELECT {_RUN}, {_AGENT}, count(*) FROM {_TABLE} "
                    f"WHERE payload->>'user_id' = %s GROUP BY 1, 2", (user_id,))
    except Exception as exc:  # noqa: BLE001
        logger.warning("resumo de memória falhou: %s", exc)
        return out
    for run_id, aid, n in rows:
        sc = _item_scope({"run_id": run_id, "agent_id": aid})
        out["total"] += n
        if sc == "global":
            out["global"] += n
        elif sc == "chat":
            out["chats"][run_id] = out["chats"].get(run_id, 0) + n
        elif sc == "model":
            out["models"][aid] = n
        elif sc == "bank":
            out["banks"][aid[len(_BANK_PREFIX):]] = n
        elif sc == "project":
            out["projects"][aid[len(_PROJECT_PREFIX):]] = n
    return out


def bank_counts(api_key: str, user_id: str) -> dict[str, int]:
    """Nº de memórias por banco (id do banco → contagem)."""
    return scope_summary(api_key, user_id).get("banks", {})


# --------------------------------------------------------------------------- #
# Escrita
# --------------------------------------------------------------------------- #
def _insert(user_id: str, text: str, *, run_id: str | None, agent_id: str | None,
            role: str | None = None) -> str:
    mem_id = str(uuid.uuid4())
    payload: dict[str, Any] = {
        "data": text,
        "hash": hashlib.md5(text.encode()).hexdigest(),
        "user_id": user_id,
        "created_at": _now(),
    }
    if run_id:
        payload["run_id"] = run_id
    if agent_id:
        payload["agent_id"] = agent_id
    if role:
        payload["role"] = role
    _run(f"INSERT INTO {_TABLE} (id, vector, payload) VALUES (%s::text::uuid, %s::text::vector, %s::text::jsonb)",
         (mem_id, _vec(text), json.dumps(payload, ensure_ascii=False)), fetch=False)
    return mem_id


def _update(user_id: str, memory_id: str, text: str) -> bool:
    patch = {"data": text, "hash": hashlib.md5(text.encode()).hexdigest(), "updated_at": _now()}
    return _exec(
        f"UPDATE {_TABLE} SET vector = %s::text::vector, payload = payload || %s::text::jsonb "
        f"WHERE id::text = %s AND payload->>'user_id' = %s",
        (_vec(text), json.dumps(patch, ensure_ascii=False), memory_id, user_id),
    ) == 1


def _delete(user_id: str, memory_id: str) -> bool:
    apagou = _exec(f"DELETE FROM {_TABLE} WHERE id::text = %s AND payload->>'user_id' = %s",
                   (memory_id, user_id)) == 1
    if apagou:
        _exec("DELETE FROM disabled_memories WHERE memory_id = %s", (memory_id,))
    return apagou


_FACTS_PROMPT = """You extract durable facts about the user from a conversation, so a \
future conversation can remember them.

Return JSON: {"facts": ["...", "..."]}

Keep only what is worth remembering later: who the user is, preferences, goals and \
plans, projects and their context, people and relationships, constraints, recurring \
situations, decisions. Skip greetings, one-off requests, general knowledge, anything \
about the assistant, and anything the user did not state or clearly imply.

Each fact is short and self-contained ("Prefers answers in Portuguese", "Is building \
a self-hosted AI app called AI Workspace"). Write every fact in the language the user \
wrote in. When nothing is worth remembering, return {"facts": []}."""

_DECIDE_PROMPT = """You keep a user's long-term memory up to date. Compare the NEW \
FACTS with the EXISTING MEMORIES and return one entry per existing memory and per new \
fact:

- ADD: new information not covered by any existing memory (use a new id, e.g. "new").
- UPDATE: same subject as an existing memory, with changed or more complete information. \
Keep the existing id and write the merged text.
- DELETE: the new fact contradicts an existing memory, which is now wrong. Keep its id.
- NONE: already known, nothing changes. Keep its id.

Return JSON: {"memory": [{"id": "...", "text": "...", "event": "ADD|UPDATE|DELETE|NONE"}]}
Keep the language of the facts."""


def _llm_json(api_key: str, system: str, user: str) -> dict:
    """Uma chamada ao OpenRouter pedindo JSON. Síncrona (roda em threadpool)."""
    import httpx

    from ..providers.openrouter import _headers

    s = get_settings()
    resp = httpx.post(
        f"{s.openrouter_base_url}/chat/completions",
        headers=_headers(api_key),
        json={
            "model": _LLM_MODEL,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        },
        timeout=_LLM_TIMEOUT,
    )
    resp.raise_for_status()
    content = ((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    content = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content.strip())
    return json.loads(content or "{}")


def _conversation(messages: list[dict[str, str]]) -> str:
    linhas = [f"{m['role']}: {m['content']}" for m in messages
              if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")]
    return "\n".join(linhas)


def add(
    api_key: str,
    messages: list[dict[str, str]],
    user_id: str,
    *,
    run_id: str | None = None,
    agent_id: str | None = None,
) -> dict | None:
    """Extrai fatos da conversa e consolida com as memórias do MESMO escopo (ADD /
    UPDATE / DELETE / NONE). Bloqueante. Devolve {results: [{id, memory, event}]}."""
    conversa = _conversation(messages)
    if not conversa:
        return {"results": []}
    try:
        hoje = datetime.now(UTC).date().isoformat()
        facts = _llm_json(api_key, _FACTS_PROMPT,
                          f"Today is {hoje}.\n\nConversation:\n{conversa}").get("facts") or []
        facts = [f.strip() for f in facts if isinstance(f, str) and f.strip()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("extração de memória falhou: %s", exc)
        return None
    if not facts:
        return {"results": []}

    cond, params = _exact_scope_sql(run_id, agent_id)
    existentes: dict[str, str] = {}
    try:
        for fato in facts:
            for i, p in _run(
                f"SELECT id, payload FROM {_TABLE} WHERE payload->>'user_id' = %s AND {cond} "
                f"ORDER BY vector <=> %s::text::vector LIMIT %s",
                [user_id, *params, _vec(fato), _SIMILAR_PER_FACT],
            ):
                existentes[str(i)] = p.get("data") or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("busca de memórias parecidas falhou: %s", exc)
        return None

    results: list[dict[str, str]] = []
    try:
        if not existentes:
            # nada parecido no escopo: tudo é novo (poupa a 2ª chamada ao LLM)
            for fato in facts:
                results.append({"id": _insert(user_id, fato, run_id=run_id, agent_id=agent_id),
                                "memory": fato, "event": "ADD"})
            return {"results": results}
        # ids curtos no prompt: o modelo inventa/estraga UUIDs, mas não "0", "1", "2"
        por_indice = dict(enumerate(existentes))
        antigos = [{"id": str(k), "text": existentes[v]} for k, v in por_indice.items()]
        decisao = _llm_json(
            api_key, _DECIDE_PROMPT,
            "EXISTING MEMORIES:\n" + json.dumps(antigos, ensure_ascii=False)
            + "\n\nNEW FACTS:\n" + json.dumps(facts, ensure_ascii=False),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("consolidação de memória falhou: %s", exc)
        return {"results": results} if results else None

    for acao in decisao.get("memory") or []:
        if not isinstance(acao, dict):
            continue
        evento = str(acao.get("event") or "").upper()
        texto = str(acao.get("text") or "").strip()
        try:
            alvo = por_indice.get(int(str(acao.get("id"))))
        except ValueError:
            alvo = None
        try:
            if evento == "ADD" and texto:
                results.append({"id": _insert(user_id, texto, run_id=run_id, agent_id=agent_id),
                                "memory": texto, "event": "ADD"})
            elif evento == "UPDATE" and texto and alvo and _update(user_id, alvo, texto):
                results.append({"id": alvo, "memory": texto, "event": "UPDATE"})
            elif evento == "DELETE" and alvo and _delete(user_id, alvo):
                results.append({"id": alvo, "memory": existentes[alvo], "event": "DELETE"})
        except Exception as exc:  # noqa: BLE001
            logger.warning("aplicar memória (%s) falhou: %s", evento, exc)
    return {"results": results}


def _new_ids(res: dict | None) -> list[str]:
    """Ids ADD/UPDATE de um retorno de `add`."""
    if not res:
        return []
    return [str(i["id"]) for i in res.get("results") or []
            if isinstance(i, dict) and i.get("id") and i.get("event") in ("ADD", "UPDATE")]


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
    """Grava a memória pós-turno no escopo escolhido pelo chat: global / model / chat /
    project (pasta) / "bank:<id>". `off` (ou escopo sem o id necessário) = não grava.
    Com `review`, as novas memórias entram PENDENTES (o modelo não as usa até aprovar)."""
    if scope == "off":
        return
    run_id = chat_id if scope == "chat" else None
    aid = agent_id if scope == "model" else None
    if scope == "project":
        aid = _PROJECT_PREFIX + project_id if project_id else None
    if scope.startswith(_BANK_PREFIX):
        aid = scope
    if scope == "chat" and not run_id:
        return
    if scope in ("model", "project") and not aid:
        return
    res = add(api_key, messages, user_id, run_id=run_id, agent_id=aid)
    if review:
        new = _new_ids(res)
        if new:
            set_pending(user_id, new)


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
    """Adiciona uma memória CRUA (sem extração por LLM) num escopo: global / model /
    chat / bank / project. Com `pending`, entra aguardando aprovação (curadoria)."""
    if not text.strip():
        return False
    run_id = chat_id if scope == "chat" else None
    aid = agent_id if scope == "model" else None
    if scope == "bank" and agent_id:
        aid = agent_id if agent_id.startswith(_BANK_PREFIX) else _BANK_PREFIX + agent_id
    if scope == "project" and agent_id:
        aid = agent_id if agent_id.startswith(_PROJECT_PREFIX) else _PROJECT_PREFIX + agent_id
    try:
        mem_id = _insert(user_id, text.strip(), run_id=run_id, agent_id=aid, role="user")
    except Exception as exc:  # noqa: BLE001
        logger.warning("add_manual falhou: %s", exc)
        return False
    if pending:
        set_pending(user_id, [mem_id])
    return True


def update_memory(api_key: str, memory_id: str, text: str, owner_user_id: str = "") -> bool:
    """Edita o texto. Só altera memória do PRÓPRIO usuário: o id é global na tabela, e
    sem dono informado falha fechado."""
    owner = str(owner_user_id or "").strip()
    if not owner or not _valid_id(memory_id) or not text.strip():
        return False
    try:
        return _update(owner, str(memory_id), text.strip())
    except Exception as exc:  # noqa: BLE001
        logger.warning("update de memória falhou: %s", exc)
        return False


def delete_memory(api_key: str, memory_id: str, owner_user_id: str = "") -> bool:
    """Apaga uma memória do PRÓPRIO usuário (falha fechado sem dono)."""
    owner = str(owner_user_id or "").strip()
    if not owner or not _valid_id(memory_id):
        return False
    try:
        return _delete(owner, str(memory_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete de memória falhou: %s", exc)
        return False


def _delete_where(user_id: str, cond: list[str], params: list) -> int:
    rows = _run(
        f"DELETE FROM {_TABLE} WHERE " + " AND ".join(["payload->>'user_id' = %s", *cond])
        + " RETURNING id::text",
        [user_id, *params],
    )
    ids = [r[0] for r in rows]
    if ids:
        _exec("DELETE FROM disabled_memories WHERE memory_id = ANY(%s::text[])", (ids,))
    return len(ids)


def delete_scope(
    api_key: str,
    user_id: str,
    *,
    scope: str,
    chat_id: str | None = None,
    agent_id: str | None = None,
) -> int:
    """Apaga TODAS as memórias de um escopo. `agent_id` é o id do modelo (model) ou
    já vem prefixado ("bank:<id>" / "project:<pasta>"). Retorna nº apagado."""
    if scope == "chat" and chat_id:
        cond, params = [f"{_RUN} = %s"], [chat_id]
    elif scope in ("model", "bank", "project") and agent_id:
        cond, params = [f"{_RUN} IS NULL AND {_AGENT} = %s"], [agent_id]
    elif scope == "global":
        cond, params = [f"{_RUN} IS NULL AND {_AGENT} IS NULL"], []
    else:
        return 0
    try:
        return _delete_where(user_id, cond, params)
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete_scope falhou: %s", exc)
        return 0


def delete_user_memories(user_id: str) -> int:
    """Apaga TODAS as memórias de um usuário (exclusão de conta). A tabela não tem FK
    para `users` — o payload guarda o id como texto —, então o CASCADE não chega aqui."""
    return _delete_where(str(user_id), [], [])
