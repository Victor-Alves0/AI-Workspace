"""Testes puros do second brain: wikilinks, chave de nota e grafo (sem DB)."""
from __future__ import annotations

from aiworkspace.knowledge.brain import build_graph, note_key, parse_links


# --------------------------------------------------------------------------- #
# note_key
# --------------------------------------------------------------------------- #
def test_note_key_normaliza_caixa_extensao_e_espacos():
    assert note_key("Minha Nota.md") == "minha nota"
    assert note_key("minha_nota") == "minha nota"
    assert note_key("  Minha   Nota .markdown ") == "minha nota"
    assert note_key("nota.txt") == "nota"


def test_note_key_vazio():
    assert note_key("") == ""
    assert note_key("   ") == ""


# --------------------------------------------------------------------------- #
# parse_links
# --------------------------------------------------------------------------- #
def test_parse_links_basico():
    assert parse_links("veja [[Nota A]] e [[Nota B]]") == ["Nota A", "Nota B"]


def test_parse_links_alias_e_heading():
    # [[Alvo|apelido]] e [[Alvo#seção]] apontam para "Alvo"
    assert parse_links("[[Nota A|apelido]] [[Nota B#contexto]]") == ["Nota A", "Nota B"]


def test_parse_links_dedup_por_chave():
    assert parse_links("[[Nota A]] [[nota_a]] [[NOTA A.md]]") == ["Nota A"]


def test_parse_links_ignora_malformados():
    assert parse_links("[[]] [[ ]] [nao é] [[quebra\nde linha]]") == []


# --------------------------------------------------------------------------- #
# build_graph
# --------------------------------------------------------------------------- #
def _doc(i: str, filename: str, text: str, title: str = ""):
    return {"id": i, "filename": filename, "title": title or filename.rsplit(".", 1)[0], "text": text}


def test_build_graph_nodes_e_edges():
    docs = [
        _doc("1", "A.md", "liga em [[B]] e [[C]]"),
        _doc("2", "B.md", "volta para [[A]]"),
        _doc("3", "C.md", "sem links"),
    ]
    g = build_graph(docs)
    assert {n["id"] for n in g["nodes"]} == {"1", "2", "3"}
    assert {(e["source"], e["target"]) for e in g["edges"]} == {("1", "2"), ("1", "3"), ("2", "1")}
    a = next(n for n in g["nodes"] if n["id"] == "1")
    assert a["links_out"] == 2 and a["links_in"] == 1


def test_build_graph_ghost_nodes():
    g = build_graph([_doc("1", "A.md", "cita [[Inexistente]]")])
    ghost = next(n for n in g["nodes"] if n["ghost"])
    assert ghost["id"] == "ghost:inexistente" and ghost["title"] == "Inexistente"
    assert g["edges"] == [{"source": "1", "target": "ghost:inexistente"}]


def test_build_graph_self_link_ignorado():
    g = build_graph([_doc("1", "A.md", "auto [[A]]")])
    assert g["edges"] == []


def test_build_graph_resolve_por_meta_title():
    # o wikilink casa com o meta.title mesmo com filename diferente
    docs = [
        _doc("1", "20260712-nota.md", "veja [[Ideias do Projeto]]"),
        _doc("2", "outra.md", "corpo", title="Ideias do Projeto"),
    ]
    g = build_graph(docs)
    assert g["edges"] == [{"source": "1", "target": "2"}]


def test_build_graph_edge_duplicada_conta_uma_vez():
    g = build_graph([
        _doc("1", "A.md", "[[B]] duas vezes [[B]]"),
        _doc("2", "B.md", ""),
    ])
    assert g["edges"] == [{"source": "1", "target": "2"}]
    b = next(n for n in g["nodes"] if n["id"] == "2")
    assert b["links_in"] == 1
