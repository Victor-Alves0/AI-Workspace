"""Anexos no histórico: arquivos pequenos voltam inteiros, grandes viram referência +
read_attachment, imagens recentes voltam como imagem, mensagem só com anexo não some."""
from __future__ import annotations

from types import SimpleNamespace as NS

from aiworkspace.chat import attachment_context as ac
from aiworkspace.chat import orchestrator as orch


def _m(role, content="", attachments=None, compacted=False):
    return NS(role=role, content=content, attachments=attachments, compacted=compacted, reasoning=None)


def _file(name, text):
    return {"type": "file", "name": name, "text": text}


async def test_arquivo_pequeno_continua_no_contexto_nas_proximas_mensagens():
    h = await ac.history([
        _m("user", "resuma", [_file("colado.txt", "O céu é verde em Marte.")]),
        _m("assistant", "Resumo: fala de Marte."),
        _m("user", "e o que mais?"),
    ])
    assert len(h) == 3
    assert "[Arquivo anexado: colado.txt]\nO céu é verde em Marte." in h[0]["content"]
    assert h[0]["content"].startswith("resuma")
    assert h[0]["_files"] == ["colado.txt"] and ac.has_files(h)


async def test_mensagem_so_com_anexo_nao_some_e_compactada_sai():
    h = await ac.history([
        _m("user", "", [_file("a.txt", "conteúdo")]),
        _m("user", "velha", [_file("b.txt", "x")], compacted=True),
        _m("assistant", ""),
    ])
    assert len(h) == 1 and h[0]["content"] == "[Arquivo anexado: a.txt]\nconteúdo"


async def test_arquivo_grande_vira_referencia_para_read_attachment():
    grande = "linha\n" * 10_000
    h = await ac.history([_m("user", "analise", [_file("log.txt", grande)])])
    assert grande not in h[0]["content"]
    assert 'read_attachment(name="log.txt")' in h[0]["content"]
    assert "60.000 caracteres" in h[0]["content"]


async def test_orcamento_total_prioriza_os_anexos_mais_recentes(monkeypatch):
    monkeypatch.setattr(ac, "HISTORY_INLINE_BUDGET", 25)
    h = await ac.history([
        _m("user", "1", [_file("velho.txt", "v" * 20)]),
        _m("user", "2", [_file("novo.txt", "n" * 20)]),
    ])
    assert "n" * 20 in h[1]["content"]
    assert "v" * 20 not in h[0]["content"] and "read_attachment" in h[0]["content"]


async def test_imagens_recentes_voltam_como_imagem_so_com_visao():
    img = {"type": "image", "name": "foto.png", "url": "data:image/png;base64,AAAA"}
    h = await ac.history([
        _m("user", "a", [img]), _m("user", "b", [img]), _m("user", "c", [img]),
    ])
    assert "_images" not in h[0] and h[1]["_images"] == h[2]["_images"] == ["data:image/png;base64,AAAA"]
    assert all("[Imagem anexada: foto.png]" in e["content"] for e in h)

    com = ac.for_provider(h, vision=True)
    assert com[2]["content"][1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    sem = ac.for_provider(h, vision=False)
    assert all(isinstance(e["content"], str) and "_images" not in e for e in sem)
    assert all("_files" not in e for e in com)


def test_read_attachment_le_por_partes_e_busca():
    texto = "a" * 20_000 + "PALAVRA-CHAVE" + "b" * 100
    parte = ac.excerpt("x.txt", texto, {})
    assert len(parte["text"]) == ac.READ_CHUNK and parte["next_offset"] == ac.READ_CHUNK
    fim = ac.excerpt("x.txt", texto, {"offset": parte["next_offset"]})
    assert fim["end"] is True
    achou = ac.excerpt("x.txt", texto, {"query": "palavra-chave"})
    assert achou["matches"] == 1 and "PALAVRA-CHAVE" in achou["passages"][0]["text"]


async def test_dispatcher_expoe_read_attachment(monkeypatch):
    from .test_tool_dispatcher import _drain, _mk

    async def fake(user_id, chat_id, args):
        return {"name": args["name"], "text": "ok", "chat": chat_id}

    monkeypatch.setattr(ac, "read_attachment", fake)
    _, result = await _drain(_mk(), "read_attachment", {"name": "a.txt"})
    assert result == {"name": "a.txt", "text": "ok", "chat": "c"}
    assert orch.attachment_context is ac
