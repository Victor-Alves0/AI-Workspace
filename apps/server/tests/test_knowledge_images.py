"""Imagens na Base de Conhecimento: indexação por nome/metadados + exibição no chat.

Antes: imagem caía no decode utf-8 tolerante → megabytes de lixo binário iam para o
chunking/embedding e a indexação morria sem marcar erro — o doc ficava PRESO em
"Indexando" (12 imagens ao vivo). E não havia como a IA mostrar a imagem no chat."""
from __future__ import annotations

from aiworkspace.chat.orchestrator import _knowledge_block_and_sources
from aiworkspace.knowledge.ingest import extract_text, is_image, _image_text


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


def test_image_text_cleans_separators_and_numbers():
    assert _image_text("GFYFGczWEAAVEMx.jpg") == "Imagem: GFYFGczWEAAVEMx"
    assert _image_text("a_b-c.10.png") == "Imagem: a b c"
    assert _image_text("") == "Imagem: imagem"


def test_text_files_still_decode():
    assert extract_text("notas.txt", "text/plain", "olá mundo".encode()) == "olá mundo"


# --------------------------- bloco p/ o modelo --------------------------------

def _res(**kw):
    base = {"chunk_id": "c1", "doc_id": "d1", "filename": "doc.txt", "mime": "text/plain",
            "ordinal": 0, "text": "trecho", "score": 0.9}
    return {**base, **kw}


def test_text_results_unchanged():
    blk, src = _knowledge_block_and_sources([_res()])
    assert blk == "[1] trecho"
    assert src[0]["title"] == "doc.txt" and "/knowledge/docs/d1/raw?t=" in src[0]["url"]


def test_image_result_teaches_the_model_to_show_it():
    blk, src = _knowledge_block_and_sources([
        _res(doc_id="d2", filename="akeno.webp", mime="image/webp", text="Imagem: akeno"),
    ])
    assert "IMAGE — Imagem: akeno" in blk
    assert "![akeno.webp](/knowledge/docs/d2/raw?t=" in blk   # markdown pronto p/ colar
    assert src[0]["title"] == "akeno.webp"


def test_mixed_results_number_sources_by_document():
    blk, src = _knowledge_block_and_sources([
        _res(),                                                     # [1] texto
        _res(doc_id="d2", filename="foto.png", mime="image/png"),   # [2] imagem
        _res(text="outro trecho do mesmo doc"),                     # [1] de novo
    ])
    assert len(src) == 2
    assert "[1] trecho" in blk and "[2] IMAGE" in blk and "[1] outro trecho" in blk


def test_missing_mime_is_treated_as_text():
    blk, _ = _knowledge_block_and_sources([_res(mime="")])
    assert "IMAGE" not in blk
