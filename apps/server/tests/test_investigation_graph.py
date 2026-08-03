"""Grafo de Investigação (investigation_service): helpers puros + um fluxo de
integração ponta a ponta contra o Postgres real.

O serviço é essencialmente consultas SQLAlchemy, então o valor está em travar o
COMPORTAMENTO: idempotência de nó, confiança que nunca rebaixa, auto-stub de
endpoint no bulk_link, vizinhança na query e o corte de arestas órfãs no visualize.
Os helpers puros rodam sempre; o fluxo de DB pula com graça se o Postgres não
estiver acessível (fora do container)."""
from __future__ import annotations

import asyncio
import uuid

import pytest

from aiworkspace import investigation_service as inv


# --------------------------------------------------------------------------- #
# Helpers puros (sem DB)
# --------------------------------------------------------------------------- #
def test_norm_conf_normaliza_e_faz_fallback():
    assert inv._norm_conf("CERTAIN") == "certain"
    assert inv._norm_conf(" possible ") == "possible"
    assert inv._norm_conf("") == "inferred"
    assert inv._norm_conf("lixo") == "inferred"
    assert inv._norm_conf(None) == "inferred"


def test_best_conf_nunca_rebaixa():
    # certain > inferred > possible; re-observar não pode reduzir a confiança
    assert inv._best_conf("certain", "possible") == "certain"
    assert inv._best_conf("possible", "certain") == "certain"
    assert inv._best_conf("inferred", "possible") == "inferred"
    assert inv._best_conf("possible", "possible") == "possible"


def test_as_uuid_tolera_lixo():
    u = uuid.uuid4()
    assert inv._as_uuid(str(u)) == u
    assert inv._as_uuid(u) == u
    assert inv._as_uuid("não-é-uuid") is None
    assert inv._as_uuid(None) is None


# --------------------------------------------------------------------------- #
# Fluxo de integração (Postgres real — pula se indisponível)
# --------------------------------------------------------------------------- #
# O engine asyncpg liga suas conexões ao event loop em que foram criadas; como cada
# teste roda seu próprio `asyncio.run` (loop novo), reusar o engine global entre
# testes dá "attached to a different loop". Por isso cada teste faz ping+fluxo+dispose
# DENTRO de um único loop, e descarta o engine no fim (o próximo teste recria o pool).
_SKIP = object()


def _run_db(flow):
    """Roda `flow` (async, recebe nada) num loop só; pula se o Postgres não responde."""
    async def _outer():
        from sqlalchemy import text
        from aiworkspace.db import SessionLocal, engine
        try:
            async with SessionLocal() as db:
                await db.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 — DB fora do ar (rodando fora do container)
            return _SKIP
        try:
            return await flow()
        finally:
            await engine.dispose()

    result = asyncio.run(_outer())
    if result is _SKIP:
        pytest.skip("Postgres indisponível (rode dentro do container)")
    return result


async def _make_user() -> str:
    from aiworkspace.db import SessionLocal
    from aiworkspace.models import User

    async with SessionLocal() as db:
        u = User(email=f"inv-test-{uuid.uuid4().hex}@example.test", hashed_password="x")
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return str(u.id)


async def _drop_user(uid: str) -> None:
    from aiworkspace.db import SessionLocal
    from aiworkspace.models import User

    async with SessionLocal() as db:
        u = await db.get(User, uuid.UUID(uid))
        if u is not None:
            await db.delete(u)  # CASCADE derruba graphs/nodes/edges
            await db.commit()


def test_full_flow_against_real_db():
    async def _flow():
        uid = await _make_user()
        try:
            # create
            g = await inv.create_graph(uid, "lab", target="10.0.0.5", kind="recon")
            gid = g["graph_id"]
            assert g["action"] == "created"

            # add_node idempotente: 2ª chamada mescla props e SOBE a confiança
            n1 = await inv.add_node(uid, gid, type="host", label="10.0.0.5",
                                    props={"os": "linux"}, confidence="possible")
            n2 = await inv.add_node(uid, gid, type="host", label="10.0.0.5",
                                    props={"ttl": 64}, confidence="certain")
            assert n1["node"]["id"] == n2["node"]["id"]  # mesmo nó
            assert n2["node"]["props"] == {"os": "linux", "ttl": 64}  # merge
            assert n2["node"]["confidence"] == "certain"  # subiu, não rebaixou

            # bulk_link: cria nós e arestas; endpoint ausente vira stub
            r = await inv.bulk_link(
                uid, gid,
                nodes=[{"type": "port", "label": "443", "confidence": "certain"}],
                edges=[
                    {"src": "host/10.0.0.5", "dst": "port/443", "rel": "exposes"},
                    {"src": "port/443", "dst": "endpoint//api/login", "rel": "serves"},
                ])
            assert r["ok"] and r["edges_added"] == 2
            # o endpoint não foi declarado em nodes → foi auto-criado como stub
            q_ep = await inv.query(uid, gid, label="/api/login")
            assert any(n["label"] == "endpoint//api/login" or "/api/login" in n["label"]
                       for n in q_ep["nodes"])

            # query por type
            hosts = await inv.query(uid, gid, type="host")
            assert len(hosts["nodes"]) == 1 and hosts["nodes"][0]["label"] == "10.0.0.5"

            # query neighbors_of o host: pega o host + a porta + a aresta 'exposes'
            nb = await inv.query(uid, gid, neighbors_of="10.0.0.5")
            labels = {n["label"] for n in nb["nodes"]}
            assert "10.0.0.5" in labels and "443" in labels
            assert any(e["rel"] == "exposes" for e in nb["edges"])

            # note: anexa achado ao nó
            note = await inv.add_note(uid, gid, node="443", text="TLS 1.2, cert autoassinado")
            assert note["ok"] and note["notes"] == 1

            # visualize: nós + arestas, sem órfãs
            viz = await inv.visualize(uid, gid)
            nid = {n["id"] for n in viz["nodes"]}
            assert viz["nodes"] and not viz["truncated"]
            assert all(e["source"] in nid and e["target"] in nid for e in viz["edges"])

            # delete
            d = await inv.delete_graph(uid, gid)
            assert d.get("deleted") == gid
            gone = await inv.visualize(uid, gid)
            assert "error" in gone
        finally:
            await _drop_user(uid)

    _run_db(_flow)


def test_bulk_link_dedup_arestas_no_mesmo_payload():
    """Duas arestas idênticas num único bulk_link NÃO podem virar linhas duplicadas
    (não há unique constraint; o dedup é em-memória por chamada, pois as arestas
    pendentes ainda não deram flush p/ o `select exists` enxergar)."""
    async def _flow():
        uid = await _make_user()
        try:
            g = await inv.create_graph(uid, "dup", target="10.0.0.9")
            gid = g["graph_id"]
            r = await inv.bulk_link(
                uid, gid,
                nodes=[{"type": "host", "label": "h"}, {"type": "port", "label": "80"}],
                edges=[
                    {"src": "host/h", "dst": "port/80", "rel": "exposes"},
                    {"src": "host/h", "dst": "port/80", "rel": "exposes"},  # duplicata literal
                ])
            assert r["ok"] and r["edges_added"] == 1  # a 2ª foi deduplicada
            viz = await inv.visualize(uid, gid)
            exposes = [e for e in viz["edges"] if e["rel"] == "exposes"]
            assert len(exposes) == 1
            # e uma 2ª chamada com a MESMA aresta também não duplica (dedup via DB)
            r2 = await inv.bulk_link(uid, gid, nodes=[],
                                     edges=[{"src": "host/h", "dst": "port/80", "rel": "exposes"}])
            assert r2["edges_added"] == 0
            viz2 = await inv.visualize(uid, gid)
            assert len([e for e in viz2["edges"] if e["rel"] == "exposes"]) == 1
        finally:
            await _drop_user(uid)

    _run_db(_flow)


def test_ownership_isolencia_entre_usuarios():
    async def _flow():
        owner = await _make_user()
        other = await _make_user()
        try:
            g = await inv.create_graph(owner, "meu", target="x")
            gid = g["graph_id"]
            # outro usuário não enxerga nem escreve no grafo alheio
            assert "error" in await inv.visualize(other, gid)
            assert "error" in await inv.add_node(other, gid, type="host", label="y")
            assert await inv.resolve_graph(other, graph_id=gid) is None
        finally:
            await _drop_user(owner)
            await _drop_user(other)

    _run_db(_flow)
