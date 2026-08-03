"""Serviço do Grafo de Investigação (ver models/investigation.py).

A IA é o EXTRATOR: ela dirige as tools existentes (code.exec.run curl/httpx/python-
socket, navegador, http.session.use, deep_search) e escreve o que observa aqui, num
grafo TIPADO e persistido — o análogo do codegraph para alvos/binários/comportamento
sem código-fonte em disco.

Engine EFÊMERA (NullPool) por operação — como o graph_service do codespace —, porque
a tool roda em threadpool + `asyncio.run` (loop novo). Todas as operações checam a
posse pelo `user_id`.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from .config import get_settings
from .models import InvestigationEdge, InvestigationGraph, InvestigationNode


# As tools rodam em THREADPOOL + `asyncio.run` (loop novo por chamada); tocar o
# `SessionLocal`/engine global — amarrado ao loop principal — dá "attached to a
# different loop". Por isso, engine EFÊMERA com NullPool por operação, igual ao
# graph_service (codespace) e ao caminho-tool do ledger_service. NullPool = nada
# de conexão em pool sobrevive ao loop.
@asynccontextmanager
async def _session():
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            yield db
    finally:
        await eng.dispose()

# tetos anti-explosão de contexto — o ganho do grafo é REDUZIR o que volta ao
# modelo, não devolver o banco inteiro.
_MAX_QUERY = 60
_MAX_VISUALIZE = 600
_MAX_BULK = 200

# ordem de confiança: um nó re-observado nunca REBAIXA sua confiança.
_CONF_RANK = {"possible": 0, "inferred": 1, "certain": 2}


def _uid(user_id) -> uuid.UUID:
    return user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))


def _as_uuid(v) -> uuid.UUID | None:
    try:
        return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))
    except (ValueError, TypeError, AttributeError):
        return None


def _norm_conf(c: str | None) -> str:
    c = (c or "").strip().lower()
    return c if c in _CONF_RANK else "inferred"


def _best_conf(a: str, b: str) -> str:
    return a if _CONF_RANK[_norm_conf(a)] >= _CONF_RANK[_norm_conf(b)] else b


def _node_dict(n: InvestigationNode) -> dict:
    return {
        "id": str(n.id), "type": n.type, "label": n.label,
        "props": n.props or {}, "confidence": n.confidence,
    }


def _edge_dict(e: InvestigationEdge) -> dict:
    return {
        "id": str(e.id), "source": str(e.src_id), "target": str(e.dst_id),
        "rel": e.rel, "props": e.props or {}, "confidence": e.confidence,
    }


# --------------------------------------------------------------------------- #
# Grafos (container)
# --------------------------------------------------------------------------- #
async def create_graph(user_id, name: str, *, target: str = "", kind: str = "generic",
                       description: str = "", chat_id: str | None = None,
                       project_id: str | None = None) -> dict:
    uid = _uid(user_id)
    async with _session() as db:
        g = InvestigationGraph(
            user_id=uid, name=(name or "investigação").strip()[:200],
            target=(target or "").strip(), kind=(kind or "generic").strip()[:16],
            description=(description or "").strip(),
            chat_id=_as_uuid(chat_id), codespace_project_id=_as_uuid(project_id),
        )
        db.add(g)
        await db.commit()
        await db.refresh(g)
        return {"graph_id": str(g.id), "name": g.name, "kind": g.kind,
                "target": g.target, "action": "created"}


async def _load_graph(db, user_id, graph_id) -> InvestigationGraph | None:
    gid = _as_uuid(graph_id)
    if gid is None:
        return None
    g = await db.get(InvestigationGraph, gid)
    if g is None or g.user_id != _uid(user_id):
        return None
    return g


async def resolve_graph(user_id, *, graph_id: str | None = None,
                        chat_id: str | None = None) -> InvestigationGraph | None:
    """Grafo alvo de uma operação: por `graph_id` (com own-check) ou, na falta dele,
    o grafo mais recente vinculado ao `chat_id`. None se nada casar."""
    uid = _uid(user_id)
    async with _session() as db:
        if graph_id:
            return await _load_graph(db, uid, graph_id)
        cid = _as_uuid(chat_id)
        if cid is None:
            return None
        return (await db.scalars(
            select(InvestigationGraph).where(
                InvestigationGraph.user_id == uid,
                InvestigationGraph.chat_id == cid,
            ).order_by(InvestigationGraph.created_at.desc()).limit(1)
        )).first()


async def list_graphs(user_id, *, chat_id: str | None = None, limit: int = 50) -> list[dict]:
    uid = _uid(user_id)
    async with _session() as db:
        stmt = select(InvestigationGraph).where(InvestigationGraph.user_id == uid)
        cid = _as_uuid(chat_id)
        if cid is not None:
            stmt = stmt.where(InvestigationGraph.chat_id == cid)
        graphs = list(await db.scalars(
            stmt.order_by(InvestigationGraph.created_at.desc()).limit(max(1, min(limit, 200)))
        ))
        out = []
        for g in graphs:
            nc = await db.scalar(select(func.count()).select_from(InvestigationNode)
                                 .where(InvestigationNode.graph_id == g.id))
            ec = await db.scalar(select(func.count()).select_from(InvestigationEdge)
                                 .where(InvestigationEdge.graph_id == g.id))
            out.append({"graph_id": str(g.id), "name": g.name, "kind": g.kind,
                        "target": g.target, "nodes": nc or 0, "edges": ec or 0,
                        "created_at": g.created_at.isoformat() if g.created_at else None})
        return out


async def delete_graph(user_id, graph_id) -> dict:
    async with _session() as db:
        g = await _load_graph(db, user_id, graph_id)
        if g is None:
            return {"error": "grafo não encontrado"}
        await db.delete(g)  # CASCADE apaga nós+arestas
        await db.commit()
        return {"deleted": str(graph_id)}


# --------------------------------------------------------------------------- #
# Nós + arestas
# --------------------------------------------------------------------------- #
async def _upsert_node(db, g: InvestigationGraph, type_: str, label: str,
                       props: dict | None, confidence: str) -> InvestigationNode:
    """Idempotente por (graph, type, label): re-observar mescla props e nunca
    rebaixa a confiança."""
    type_ = (type_ or "node").strip()[:48]
    label = (label or "").strip()
    conf = _norm_conf(confidence)
    existing = (await db.scalars(
        select(InvestigationNode).where(
            InvestigationNode.graph_id == g.id,
            InvestigationNode.type == type_,
            InvestigationNode.label == label,
        ).limit(1)
    )).first()
    if existing is not None:
        if props:
            merged = dict(existing.props or {})
            merged.update(props)
            existing.props = merged
        existing.confidence = _best_conf(existing.confidence, conf)
        return existing
    node = InvestigationNode(
        graph_id=g.id, user_id=g.user_id, type=type_, label=label,
        props=props or {}, confidence=conf,
    )
    db.add(node)
    await db.flush()  # popula node.id p/ arestas na mesma transação
    return node


async def _resolve_ref(db, g: InvestigationGraph, ref: str) -> InvestigationNode | None:
    """Resolve um endpoint de aresta: por id (uuid), por 'type/label', ou por label."""
    ref = (ref or "").strip()
    if not ref:
        return None
    rid = _as_uuid(ref)
    if rid is not None:
        n = await db.get(InvestigationNode, rid)
        return n if (n is not None and n.graph_id == g.id) else None
    type_hint = ""
    label = ref
    if "/" in ref:  # "host/10.0.0.1" — type-qualificado, desambigua
        maybe_type, _, rest = ref.partition("/")
        if maybe_type and rest:
            type_hint, label = maybe_type.strip(), rest.strip()
    stmt = select(InvestigationNode).where(
        InvestigationNode.graph_id == g.id, InvestigationNode.label == label)
    if type_hint:
        stmt = stmt.where(InvestigationNode.type == type_hint)
    return (await db.scalars(stmt.limit(1))).first()


async def add_node(user_id, graph_id, *, type: str, label: str,
                   props: dict | None = None, confidence: str = "inferred") -> dict:
    async with _session() as db:
        g = await _load_graph(db, user_id, graph_id)
        if g is None:
            return {"error": "grafo não encontrado"}
        if not (label or "").strip():
            return {"error": "label é obrigatório"}
        n = await _upsert_node(db, g, type, label, props, confidence)
        await db.commit()
        await db.refresh(n)
        return {"node": _node_dict(n)}


async def add_edge(user_id, graph_id, *, src: str, dst: str, rel: str,
                   props: dict | None = None, confidence: str = "inferred") -> dict:
    async with _session() as db:
        g = await _load_graph(db, user_id, graph_id)
        if g is None:
            return {"error": "grafo não encontrado"}
        sn = await _resolve_ref(db, g, src)
        dn = await _resolve_ref(db, g, dst)
        if sn is None or dn is None:
            missing = src if sn is None else dst
            return {"error": f"nó '{missing}' não encontrado — crie-o antes (add_node) "
                             "ou use 'link' para criar nós e arestas de uma vez"}
        rel = (rel or "rel").strip()[:48]
        existing = (await db.scalars(
            select(InvestigationEdge).where(
                InvestigationEdge.graph_id == g.id,
                InvestigationEdge.src_id == sn.id,
                InvestigationEdge.dst_id == dn.id,
                InvestigationEdge.rel == rel,
            ).limit(1)
        )).first()
        if existing is not None:
            if props:
                merged = dict(existing.props or {})
                merged.update(props)
                existing.props = merged
            existing.confidence = _best_conf(existing.confidence, _norm_conf(confidence))
            edge = existing
        else:
            edge = InvestigationEdge(
                graph_id=g.id, user_id=g.user_id, src_id=sn.id, dst_id=dn.id,
                rel=rel, props=props or {}, confidence=_norm_conf(confidence))
            db.add(edge)
        await db.commit()
        await db.refresh(edge)
        return {"edge": _edge_dict(edge)}


async def bulk_link(user_id, graph_id, *, nodes: list[dict] | None,
                    edges: list[dict] | None) -> dict:
    """Adiciona vários nós e arestas numa transação — o caminho barato pro loop.

    `nodes`: [{type, label, props?, confidence?}]. `edges`: [{src, dst, rel, props?,
    confidence?}] onde src/dst são id, 'type/label' ou label; um endpoint ausente é
    criado como stub (type='node', confidence='possible') — tolerante."""
    nodes = (nodes or [])[:_MAX_BULK]
    edges = (edges or [])[:_MAX_BULK]
    async with _session() as db:
        g = await _load_graph(db, user_id, graph_id)
        if g is None:
            return {"error": "grafo não encontrado"}
        n_added = 0
        for nd in nodes:
            if not (nd.get("label") or "").strip():
                continue
            await _upsert_node(db, g, nd.get("type") or "node", nd.get("label"),
                               nd.get("props"), nd.get("confidence") or "inferred")
            n_added += 1
        e_added = 0
        # arestas add-adas neste loop ainda não deram flush, então o `select exists`
        # abaixo não as enxerga — sem este set, duas arestas idênticas no MESMO payload
        # criariam linhas duplicadas (não há unique constraint). Dedup em-memória por chamada.
        seen: set = set()
        for ed in edges:
            sref, dref = (ed.get("src") or ""), (ed.get("dst") or "")
            if not sref or not dref:
                continue
            sn = await _resolve_ref(db, g, sref)
            if sn is None:
                sn = await _upsert_node(db, g, "node", sref, None, "possible")
            dn = await _resolve_ref(db, g, dref)
            if dn is None:
                dn = await _upsert_node(db, g, "node", dref, None, "possible")
            rel = (ed.get("rel") or "rel").strip()[:48]
            key = (sn.id, dn.id, rel)
            if key in seen:
                continue
            seen.add(key)
            exists = (await db.scalars(
                select(InvestigationEdge).where(
                    InvestigationEdge.graph_id == g.id,
                    InvestigationEdge.src_id == sn.id,
                    InvestigationEdge.dst_id == dn.id,
                    InvestigationEdge.rel == rel,
                ).limit(1)
            )).first()
            if exists is None:
                db.add(InvestigationEdge(
                    graph_id=g.id, user_id=g.user_id, src_id=sn.id, dst_id=dn.id,
                    rel=rel, props=ed.get("props") or {},
                    confidence=_norm_conf(ed.get("confidence"))))
                e_added += 1
        await db.commit()
        return {"ok": True, "nodes_added": n_added, "edges_added": e_added,
                "graph_id": str(g.id)}


async def add_note(user_id, graph_id, *, node: str, text: str) -> dict:
    """Anexa uma observação/achado ao nó (acumulada em props['notes'])."""
    async with _session() as db:
        g = await _load_graph(db, user_id, graph_id)
        if g is None:
            return {"error": "grafo não encontrado"}
        n = await _resolve_ref(db, g, node)
        if n is None:
            return {"error": f"nó '{node}' não encontrado"}
        txt = (text or "").strip()
        if not txt:
            return {"error": "text é obrigatório"}
        props = dict(n.props or {})
        notes = list(props.get("notes") or [])
        notes.append(txt[:2000])
        props["notes"] = notes[-50:]
        n.props = props
        await db.commit()
        return {"ok": True, "node": str(n.id), "notes": len(notes)}


# --------------------------------------------------------------------------- #
# Consulta + visualização
# --------------------------------------------------------------------------- #
async def query(user_id, graph_id, *, type: str = "", rel: str = "", label: str = "",
                neighbors_of: str = "", limit: int = _MAX_QUERY) -> dict:
    """Consulta o grafo: filtra nós por type/label, arestas por rel, ou a vizinhança
    de um nó (neighbors_of = id/label). Sempre capado."""
    limit = max(1, min(int(limit or _MAX_QUERY), _MAX_QUERY))
    async with _session() as db:
        g = await _load_graph(db, user_id, graph_id)
        if g is None:
            return {"error": "grafo não encontrado"}

        if neighbors_of:
            center = await _resolve_ref(db, g, neighbors_of)
            if center is None:
                return {"error": f"nó '{neighbors_of}' não encontrado"}
            edges = list(await db.scalars(
                select(InvestigationEdge).where(
                    InvestigationEdge.graph_id == g.id,
                    (InvestigationEdge.src_id == center.id)
                    | (InvestigationEdge.dst_id == center.id),
                ).limit(limit)))
            nid = {center.id}
            for e in edges:
                nid.add(e.src_id)
                nid.add(e.dst_id)
            nodes = list(await db.scalars(
                select(InvestigationNode).where(InvestigationNode.id.in_(nid))))
            return {"center": _node_dict(center),
                    "nodes": [_node_dict(n) for n in nodes],
                    "edges": [_edge_dict(e) for e in edges]}

        if rel:
            edges = list(await db.scalars(
                select(InvestigationEdge).where(
                    InvestigationEdge.graph_id == g.id,
                    InvestigationEdge.rel == rel.strip(),
                ).limit(limit)))
            return {"edges": [_edge_dict(e) for e in edges]}

        stmt = select(InvestigationNode).where(InvestigationNode.graph_id == g.id)
        if type:
            stmt = stmt.where(InvestigationNode.type == type.strip())
        if label:
            stmt = stmt.where(InvestigationNode.label.ilike(f"%{label.strip()}%"))
        nodes = list(await db.scalars(stmt.limit(limit)))
        return {"nodes": [_node_dict(n) for n in nodes], "count": len(nodes)}


async def visualize(user_id, graph_id) -> dict:
    """Nós + arestas completos p/ o canvas (capado em _MAX_VISUALIZE nós)."""
    async with _session() as db:
        g = await _load_graph(db, user_id, graph_id)
        if g is None:
            return {"error": "grafo não encontrado"}
        nodes = list(await db.scalars(
            select(InvestigationNode).where(InvestigationNode.graph_id == g.id)
            .limit(_MAX_VISUALIZE)))
        nid = {n.id for n in nodes}
        edges = list(await db.scalars(
            select(InvestigationEdge).where(InvestigationEdge.graph_id == g.id)))
        # só arestas cujos dois lados sobreviveram ao teto de nós
        edges = [e for e in edges if e.src_id in nid and e.dst_id in nid]
        return {
            "graph": {"id": str(g.id), "name": g.name, "kind": g.kind,
                      "target": g.target, "description": g.description},
            "nodes": [_node_dict(n) for n in nodes],
            "edges": [_edge_dict(e) for e in edges],
            "truncated": len(nodes) >= _MAX_VISUALIZE,
        }
