"""Imagens na Base de Conhecimento: indexação por nome/metadados + exibição no chat.

Antes: imagem caía no decode utf-8 tolerante → megabytes de lixo binário iam para o
chunking/embedding e a indexação morria sem marcar erro — o doc ficava PRESO em
"Indexando" (12 imagens ao vivo). E não havia como a IA mostrar a imagem no chat."""

from __future__ import annotations

import pytest

from aiworkspace.chat import orchestrator
from aiworkspace.chat.orchestrator import _knowledge_block_and_sources
from aiworkspace.knowledge.ingest import _image_text, extract_text, is_image

# ------------------------------- ingestão ------------------------------------

def test_image_detected_by_mime_and_extension():
    assert is_image("foto.png", "")
    assert is_image("x", "image/webp")
    assert is_image("FOTO.JPEG", "application/octet-stream")
    assert not is_image("doc.pdf", "application/pdf")
    assert not is_image("notas.txt", "text/plain")


def test_image_bytes_are_never_decoded():
    # bytes de imagem (inválidos como utf-8) não podem virar texto-lixo
    fake_jpeg = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 100
    out = extract_text("akeno-himejima-high-school-dxd-set-2-138906269-27.webp", "image/webp", fake_jpeg)
    assert out.startswith("Imagem: ")
    assert "akeno himejima high school dxd set" in out
    assert "�" not in out          # nenhum replacement char (lixo binário)


def test_image_text_cleans_separators_but_preserves_sequence_numbers():
    assert _image_text("GFYFGczWEAAVEMx.jpg") == "Imagem: GFYFGczWEAAVEMx"
    assert _image_text("a_b-c.10.png") == "Imagem: a b c 10"
    assert _image_text("") == "Imagem: imagem"


def test_text_files_still_decode():
    assert extract_text("notas.txt", "text/plain", "olá mundo".encode()) == "olá mundo"


# --------------------------- bloco p/ o modelo --------------------------------

def _res(**kw):
    base = {"chunk_id": "c1", "doc_id": "d1", "filename": "doc.txt", "mime": "text/plain",
            "ordinal": 0, "text": "trecho", "score": 0.9}
    return {**base, **kw}


def test_text_result_teaches_model_to_attach_original_file():
    blk, src = _knowledge_block_and_sources([_res()])
    # cada item nota sua localização "(in: [pasta/]arquivo)" p/ a IA atender pedidos por pasta
    assert blk.startswith("[1] (in: doc.txt) trecho")
    assert "SEND/ATTACH" in blk
    assert "[doc.txt](/knowledge/docs/d1/raw)" in blk
    assert "?t=" not in blk  # JWT longo não é desperdiçado no contexto do modelo
    assert src[0]["title"] == "doc.txt" and "/knowledge/docs/d1/raw?t=" in src[0]["url"]


def test_image_result_teaches_the_model_to_show_it():
    blk, src = _knowledge_block_and_sources([
        _res(doc_id="d2", filename="akeno.webp", mime="image/webp", text="Imagem: akeno"),
    ])
    assert "IMAGE (in: akeno.webp) — Imagem: akeno" in blk
    assert "![akeno.webp](/knowledge/docs/d2/raw)" in blk   # markdown curto pronto p/ colar
    assert src[0]["title"] == "akeno.webp"


def test_mixed_results_number_sources_by_document():
    blk, src = _knowledge_block_and_sources([
        _res(),                                                     # [1] texto
        _res(doc_id="d2", filename="foto.png", mime="image/png"),   # [2] imagem
        _res(text="outro trecho do mesmo doc"),                     # [1] de novo
    ])
    assert len(src) == 2
    assert "[1] (in: doc.txt) trecho" in blk and "[2] IMAGE" in blk \
        and "[1] (in: doc.txt) outro trecho" in blk


def test_missing_mime_is_treated_as_text():
    blk, _ = _knowledge_block_and_sources([_res(mime="")])
    assert "IMAGE" not in blk


# ---------------------- reconciliação no fim do stream ----------------------

@pytest.mark.asyncio
async def test_resign_repairs_mutated_uuid_from_turn_source(monkeypatch):
    real = "e30cdad6-bd5a-4ba2-af57-670d9dee2ea2"
    mutated = "e30cdad6-bd5a-4ba2-af57-670d9dee2ea3"
    events = [{
        "kind": "result", "name": "knowledge", "data": {
            "sources": [{"title": "Fegalvao XXX 3.mp4", "url": f"/knowledge/docs/{real}/raw?t=old"}],
        },
    }]
    monkeypatch.setattr(orchestrator, "sign_doc_url", lambda doc_id: f"/knowledge/docs/{doc_id}/raw?t=fresh")

    async def none_exist(_ids, _user_id):
        return set()

    monkeypatch.setattr(orchestrator, "_existing_kb_doc_ids", none_exist)
    text = f"Antes\n\n![Fegalvao XXX 3.mp4](/knowledge/docs/{mutated}/raw?t=damaged)"

    out = await orchestrator._resign_kb_images(text, events)

    assert mutated not in out
    assert f"![Fegalvao XXX 3.mp4](/knowledge/docs/{real}/raw?t=fresh)" in out


@pytest.mark.asyncio
async def test_resign_normalizes_uuid_case_and_keeps_multiple_media(monkeypatch):
    first = "E30CDAD6-BD5A-4BA2-AF57-670D9DEE2EA2"
    second = "11D4B00C-83C5-42E8-9D0D-DDA2F8187A96"
    canonical = {first.lower(), second.lower()}
    monkeypatch.setattr(orchestrator, "sign_doc_url", lambda doc_id: f"/knowledge/docs/{doc_id}/raw?t=fresh")

    async def all_exist(ids, _user_id):
        assert ids == canonical
        return canonical

    monkeypatch.setattr(orchestrator, "_existing_kb_doc_ids", all_exist)
    text = (
        f"![um.mp4](/knowledge/docs/{first}/raw?t=bad)\n\n"
        f"![dois.mp4](/knowledge/docs/{second}/raw?t=bad)"
    )

    out = await orchestrator._resign_kb_images(text)

    assert out.count("?t=fresh") == 2
    assert first not in out and second not in out


@pytest.mark.asyncio
async def test_resign_removes_untrusted_reference_as_one_markdown_node(monkeypatch):
    fake = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

    async def none_exist(_ids, _user_id):
        return set()

    monkeypatch.setattr(orchestrator, "_existing_kb_doc_ids", none_exist)
    out = await orchestrator._resign_kb_images(
        f"Antes\n\n![inventado.mp4](/knowledge/docs/{fake}/raw?t=x)\n\nDepois"
    )

    assert "inventado" not in out
    assert "![]" not in out and "]()" not in out
    assert "Antes" in out and "Depois" in out
