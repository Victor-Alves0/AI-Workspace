"""Memória nativa (sem mem0) contra o Postgres real: escopos, flags, posse, consolidação
e compatibilidade com as linhas que o mem0 gravou. Ver conftest: pula sem TEST_DATABASE_URL.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from .conftest import migrar, pytestmark  # noqa: F401


@pytest.fixture
def mem(banco, engine, monkeypatch):
    """memory_service apontado para o banco de teste já migrado + dois usuários."""
    from aiworkspace.config import get_settings
    from aiworkspace.memory import memory_service
    from aiworkspace.models import User

    migrar(engine, "head")
    monkeypatch.setattr(get_settings(), "database_url", banco, raising=False)
    with Session(engine) as s:
        a = User(email=f"a{uuid.uuid4().hex[:6]}@t.local", hashed_password="x")
        b = User(email=f"b{uuid.uuid4().hex[:6]}@t.local", hashed_password="x")
        s.add_all([a, b])
        s.commit()
        ids = str(a.id), str(b.id)
    return memory_service, ids


def _textos(rows) -> set[str]:
    return {r["text"] for r in rows}


def test_busca_do_turno_so_ve_os_escopos_ligados(mem):
    ms, (a, b) = mem
    ms.add_manual("k", a, "gosta de café forte", scope="global")
    ms.add_manual("k", a, "neste chat fala de café", scope="chat", chat_id="chat-A")
    ms.add_manual("k", a, "no outro chat também café", scope="chat", chat_id="chat-B")
    ms.add_manual("k", a, "o modelo X prefere café", scope="model", agent_id="modelo-X")
    ms.add_manual("k", a, "o modelo Y detesta café", scope="model", agent_id="modelo-Y")
    ms.add_manual("k", a, "banco de receitas de café", scope="bank", agent_id="banco-1")
    ms.add_manual("k", a, "projeto de uma cafeteria", scope="project", agent_id="pasta-1")
    ms.add_manual("k", b, "OUTRO usuário ama café", scope="global")

    achou = ms.search_for_turn(
        "k", "café", a, chat_id="chat-A", agent_id="modelo-X",
        read={"global": True, "chat": True, "model": True, "project": False},
        banks=["banco-1"], project_id="pasta-1", limit=20,
    )

    assert _textos(achou) == {"gosta de café forte", "neste chat fala de café",
                              "o modelo X prefere café", "banco de receitas de café"}
    escopos = {r["text"]: r["scope"] for r in achou}
    assert escopos["banco de receitas de café"] == "bank"

    # com o projeto ligado, a memória da pasta entra
    com_projeto = ms.search_for_turn("k", "café", a, chat_id=None, agent_id=None,
                                     read={"project": True}, project_id="pasta-1")
    assert _textos(com_projeto) == {"projeto de uma cafeteria"}


def test_desativada_e_pendente_ficam_fora_da_busca_mas_aparecem_na_lista(mem):
    ms, (a, _) = mem
    ms.add_manual("k", a, "memória ativa sobre gatos", scope="global")
    ms.add_manual("k", a, "memória pendente sobre gatos", scope="global", pending=True)
    ms.add_manual("k", a, "memória desativada sobre gatos", scope="global")
    alvo = next(r["id"] for r in ms.list_memories("k", a) if "desativada" in r["text"])
    ms.set_disabled(a, [alvo], True)

    busca = ms.search_for_turn("k", "gatos", a, chat_id=None, agent_id=None, read={"global": True})
    assert _textos(busca) == {"memória ativa sobre gatos"}

    lista = {r["text"]: r for r in ms.list_memories("k", a)}
    assert lista["memória desativada sobre gatos"]["disabled"] is True
    assert lista["memória pendente sobre gatos"]["pending"] is True


def test_lista_resumo_e_filtros(mem):
    ms, (a, _) = mem
    ms.add_manual("k", a, "g1", scope="global")
    ms.add_manual("k", a, "c1", scope="chat", chat_id="c")
    ms.add_manual("k", a, "m1", scope="model", agent_id="m")
    ms.add_manual("k", a, "b1", scope="bank", agent_id="bk")
    ms.add_manual("k", a, "p1", scope="project", agent_id="pj")

    assert _textos(ms.list_memories("k", a, scope="global")) == {"g1"}
    assert _textos(ms.list_memories("k", a, chat_id="c")) == {"c1"}
    assert _textos(ms.list_memories("k", a, agent_id="m")) == {"m1"}
    assert _textos(ms.list_memories("k", a, bank_id="bk")) == {"b1"}
    assert _textos(ms.list_memories("k", a, project_id="pj")) == {"p1"}
    assert ms.scope_summary("k", a) == {
        "global": 1, "models": {"m": 1}, "chats": {"c": 1}, "banks": {"bk": 1},
        "projects": {"pj": 1}, "total": 5,
    }
    assert ms.bank_counts("k", a) == {"bk": 1}


def test_editar_e_apagar_so_do_proprio_dono(mem):
    ms, (a, b) = mem
    ms.add_manual("k", a, "segredo do A", scope="global")
    mid = ms.list_memories("k", a)[0]["id"]

    assert ms.update_memory("k", mid, "invadido", b) is False
    assert ms.delete_memory("k", mid, b) is False
    assert ms.delete_memory("k", mid, "") is False            # sem dono: falha fechado
    assert ms.update_memory("k", "nao-e-uuid", "x", a) is False
    assert _textos(ms.list_memories("k", a)) == {"segredo do A"}

    assert ms.update_memory("k", mid, "segredo editado", a) is True
    assert _textos(ms.list_memories("k", a)) == {"segredo editado"}
    assert ms.delete_memory("k", mid, a) is True
    assert ms.list_memories("k", a) == []


def test_apagar_escopo_e_conta_nao_levam_dado_de_outro(mem):
    ms, (a, b) = mem
    ms.add_manual("k", a, "chat 1", scope="chat", chat_id="c1")
    ms.add_manual("k", a, "chat 2", scope="chat", chat_id="c2", pending=True)
    ms.add_manual("k", a, "global do A", scope="global")
    ms.add_manual("k", b, "chat 1 do B", scope="chat", chat_id="c1")

    assert ms.delete_scope("k", a, scope="chat", chat_id="c1") == 1
    assert _textos(ms.list_memories("k", a)) == {"chat 2", "global do A"}
    assert _textos(ms.list_memories("k", b)) == {"chat 1 do B"}

    assert ms.delete_user_memories(a) == 2
    assert ms.list_memories("k", a) == [] and ms._flag_ids(a) == set()
    assert _textos(ms.list_memories("k", b)) == {"chat 1 do B"}


def test_consolidacao_adiciona_atualiza_e_apaga_no_mesmo_escopo(mem, monkeypatch):
    ms, (a, _) = mem
    chamadas: list[str] = []

    def llm(api_key, system, user):
        chamadas.append(system[:20])
        if "extract durable facts" in system:
            return {"facts": respostas.pop(0)}
        return decisao.pop(0)

    monkeypatch.setattr(ms, "_llm_json", llm)
    conversa = [{"role": "user", "content": "oi"}, {"role": "assistant", "content": "olá"}]

    # 1) nada parecido no escopo: grava direto, sem a 2ª chamada ao LLM
    respostas, decisao = [["Mora em Curitiba"]], []
    r = ms.add("k", conversa, a, run_id="chat-1")
    assert [x["event"] for x in r["results"]] == ["ADD"] and len(chamadas) == 1

    # 2) fato em conflito: o LLM decide UPDATE do existente ("0") + ADD do novo
    respostas = [["Mudou-se para Porto Alegre", "Tem um gato"]]
    decisao = [{"memory": [
        {"id": "0", "text": "Mora em Porto Alegre (mudou de Curitiba)", "event": "UPDATE"},
        {"id": "new", "text": "Tem um gato", "event": "ADD"},
    ]}]
    ms.add_scoped("k", conversa, a, scope="chat", chat_id="chat-1", review=True)
    rows = {x["text"]: x for x in ms.list_memories("k", a, chat_id="chat-1")}
    assert set(rows) == {"Mora em Porto Alegre (mudou de Curitiba)", "Tem um gato"}
    assert all(x["pending"] for x in rows.values())      # review=True → pendentes

    # 3) a consolidação não enxerga outro escopo: memória global igual não vira alvo
    ms.add_manual("k", a, "Mora em Curitiba", scope="global")
    respostas, decisao = [["Mora em Curitiba"]], []
    chamadas.clear()
    r = ms.add("k", conversa, a, run_id="chat-2")
    assert [x["event"] for x in r["results"]] == ["ADD"] and len(chamadas) == 1

    # 4) DELETE
    respostas = [["Não tem mais gato"]]
    decisao = [{"memory": [{"id": "0", "text": "Tem um gato", "event": "DELETE"}]}]
    r = ms.add("k", conversa, a, run_id="chat-1")
    assert "DELETE" in [x["event"] for x in r["results"]]
    assert "Tem um gato" not in _textos(ms.list_memories("k", a, chat_id="chat-1"))


def test_linhas_gravadas_pelo_mem0_continuam_legiveis(mem, engine):
    """Mesma tabela e mesmo payload do mem0: quem atualiza não perde as memórias."""
    ms, (a, _) = mem
    from aiworkspace.knowledge.embeddings import embed_query, to_pgvector

    payload = {"data": "Prefere respostas curtas", "hash": "x", "user_id": a,
               "agent_id": "bank:b1", "created_at": "2026-08-01T10:00:00-07:00"}
    with engine.begin() as c:
        c.execute(text("INSERT INTO aiworkspace_memories (id, vector, payload) "
                       "VALUES (:i, CAST(:v AS vector), CAST(:p AS jsonb))"),
                  {"i": str(uuid.uuid4()), "v": to_pgvector(embed_query(payload["data"])),
                   "p": json.dumps(payload)})

    [linha] = ms.list_memories("k", a)
    assert linha["scope"] == "bank" and linha["bank_id"] == "b1"
    achou = ms.search_for_turn("k", "respostas curtas", a, chat_id=None, agent_id=None,
                               read={}, banks=["b1"])
    assert _textos(achou) == {"Prefere respostas curtas"}


def test_migracao_mantem_a_tabela_que_o_mem0_criou(engine):
    """Instalação que já tinha a tabela (criada pelo mem0, sem índice) com memórias."""
    migrar(engine, "0078_chat_mini_app")
    with engine.begin() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        c.execute(text("CREATE TABLE aiworkspace_memories (id UUID PRIMARY KEY, "
                       "vector vector(384), payload JSONB)"))
        c.execute(text("INSERT INTO aiworkspace_memories (id, payload) "
                       "VALUES (:i, CAST(:p AS jsonb))"),
                  {"i": str(uuid.uuid4()), "p": json.dumps({"data": "antiga", "user_id": "u"})})
    migrar(engine, "head")
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM aiworkspace_memories")).scalar() == 1
        assert c.execute(text("SELECT count(*) FROM pg_indexes "
                              "WHERE indexname = 'ix_aiworkspace_memories_user'")).scalar() == 1
