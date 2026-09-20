"""Anexos do chat: arquivo no disco, mensagem com a referência.

O que estes testes protegem é o motivo de o módulo existir: **o binário não pode voltar
para dentro da mensagem**. Enquanto o anexo viajava embutido em base64, o teto real era
a memória do servidor — um arquivo grande derrubava o processo em vez de ser recusado.

Herméticos: disco em `tmp_path`, sem banco (as funções puras recebem/retornam objetos).
"""
from __future__ import annotations

import uuid

import pytest

from aiworkspace import uploads_service as svc
from aiworkspace.config import get_settings
from aiworkspace.models import Upload


@pytest.fixture()
def uploads_dir(tmp_path, monkeypatch):
    """Aponta o armazenamento para um diretório temporário do teste."""
    settings = get_settings()
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path), raising=False)
    return tmp_path


async def _chunks(*blocos: bytes):
    for bloco in blocos:
        yield bloco


class _FakeDb:
    """Sessão mínima: guarda o que foi adicionado, sem Postgres."""

    def __init__(self, usado: int = 0):
        self.added: list = []
        self.usado = usado

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        return None

    async def refresh(self, row):
        return row

    async def scalar(self, _stmt):
        return self.usado


# --------------------------------------------------------------------------- #
# Classificação e tetos                                                        #
# --------------------------------------------------------------------------- #
def test_tipo_do_arquivo_decide_o_teto():
    """Imagem e áudio viajam INTEIROS até o provedor, então têm teto próprio, menor.
    Um documento vira texto no servidor — só o texto segue — e por isso pode ser
    grande."""
    s = get_settings()
    assert svc.kind_for("foto.png", "image/png") == "image"
    assert svc.kind_for("voz.mp3", "audio/mpeg") == "audio"
    assert svc.kind_for("contrato.pdf", "application/pdf") == "file"
    # sem mime confiável (alguns navegadores mandam vazio), decide pela extensão
    assert svc.kind_for("foto.jpg", "") == "image"

    assert svc.max_bytes_for("image") == s.upload_image_max_bytes
    assert svc.max_bytes_for("audio") == s.upload_audio_max_bytes
    assert svc.max_bytes_for("file") == s.upload_max_bytes
    assert svc.max_bytes_for("file") > svc.max_bytes_for("image")


@pytest.mark.asyncio
async def test_arquivo_e_gravado_em_pedacos_no_disco(uploads_dir):
    db = _FakeDb()
    row = await svc.store_stream(
        db, uuid.uuid4(), "contrato.pdf", "application/pdf",
        _chunks(b"%PDF-1.7", b" conteudo", b" final"),
    )

    assert row.size == len(b"%PDF-1.7 conteudo final")
    assert row.kind == "file"
    destino = uploads_dir / row.path
    assert destino.read_bytes() == b"%PDF-1.7 conteudo final"
    assert db.added == [row]


@pytest.mark.asyncio
async def test_estourar_o_teto_aborta_e_nao_deixa_meio_arquivo(uploads_dir, monkeypatch):
    """O corte acontece DURANTE a escrita (o tamanho real só se conhece no fim). O
    pedaço já gravado tem de sumir — senão cada tentativa recusada ocupa disco."""
    monkeypatch.setattr(get_settings(), "upload_image_max_bytes", 10, raising=False)
    db = _FakeDb()

    with pytest.raises(svc.UploadTooLarge) as erro:
        await svc.store_stream(
            db, uuid.uuid4(), "foto.png", "image/png", _chunks(b"x" * 8, b"y" * 8),
        )

    assert "Imagem" in erro.value.message and "MB" in erro.value.message
    assert db.added == []
    assert not list(uploads_dir.rglob("*")) or not any(p.is_file() for p in uploads_dir.rglob("*"))


@pytest.mark.asyncio
async def test_cota_do_usuario_recusa_sem_guardar(uploads_dir, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_quota_bytes", 100, raising=False)
    db = _FakeDb(usado=95)

    with pytest.raises(svc.UploadQuotaExceeded):
        await svc.store_stream(db, uuid.uuid4(), "a.pdf", "application/pdf", _chunks(b"z" * 50))

    assert db.added == []
    assert not any(p.is_file() for p in uploads_dir.rglob("*"))


@pytest.mark.asyncio
async def test_arquivo_vazio_e_recusado(uploads_dir):
    with pytest.raises(svc.UploadEmpty):
        await svc.store_stream(_FakeDb(), uuid.uuid4(), "vazio.txt", "text/plain", _chunks(b""))


# --------------------------------------------------------------------------- #
# O que fica gravado na mensagem                                               #
# --------------------------------------------------------------------------- #
def test_mensagem_guarda_referencia_e_nunca_o_base64():
    """O anexo preparado p/ o turno leva a imagem embutida (é assim que ela chega ao
    provedor). Gravar isso devolveria o base64 para a linha da mensagem — exatamente o
    que tornava o anexo caro e limitado."""
    gigante = "data:image/png;base64," + "A" * 100_000
    preparado = [
        {"type": "image", "name": "foto.png", "upload_id": "11111111-1111-1111-1111-111111111111",
         "url": gigante},
        {"type": "file", "name": "contrato.pdf", "upload_id": "22222222-2222-2222-2222-222222222222",
         "text": "texto extraído " * 500},
    ]
    gravado = svc.persistable(preparado)

    inteiro = str(gravado)
    assert "base64" not in inteiro
    assert "texto extraído" not in inteiro
    assert len(inteiro) < 1000                      # a linha da mensagem fica minúscula
    assert gravado[0]["upload_id"] == "11111111-1111-1111-1111-111111111111"
    assert gravado[0]["url"].startswith("/uploads/11111111-1111-1111-1111-111111111111?t=")


def test_anexo_antigo_continua_gravado_como_estava():
    """Mensagens antigas (base64 embutido) precisam seguir funcionando: o formato novo
    convive com o velho, não o substitui à força."""
    antigo = [{"type": "image", "name": "foto.png", "url": "data:image/png;base64,AAA"}]
    assert svc.persistable(antigo) == antigo


def test_url_assinada_so_vale_para_o_proprio_arquivo():
    """O token é uma URL-capacidade: serve em <img> sem cookie, mas não pode abrir
    OUTRO arquivo."""
    meu, outro = str(uuid.uuid4()), str(uuid.uuid4())
    token = svc.sign_url(meu).split("t=", 1)[1]

    assert svc.verify_token(meu, token) is True
    assert svc.verify_token(outro, token) is False
    assert svc.verify_token(meu, "token-inventado") is False


# --------------------------------------------------------------------------- #
# Leitura e faxina                                                             #
# --------------------------------------------------------------------------- #
def test_leitura_recusa_arquivo_acima_do_limite_em_vez_de_carregar(uploads_dir):
    """`read_bytes` é o único ponto que traz o arquivo para a memória. Um vídeo de
    500MB não pode ser carregado só porque alguém pediu os bytes."""
    row = Upload(id=uuid.uuid4(), user_id=uuid.uuid4(), filename="grande.bin",
                 mime="application/octet-stream", size=0, kind="file", path="u/grande")
    destino = uploads_dir / row.path
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(b"x" * 5000)

    assert svc.read_bytes(row, limit=1000) is None
    assert svc.read_bytes(row, limit=10_000) == b"x" * 5000


def test_arquivo_sumido_do_disco_nao_derruba_a_leitura(uploads_dir):
    """Restore de banco sem o volume, faxina manual: a linha existe e o arquivo não."""
    row = Upload(id=uuid.uuid4(), user_id=uuid.uuid4(), filename="sumiu.pdf",
                 mime="application/pdf", size=10, kind="file", path="u/sumiu")
    assert svc.read_bytes(row) is None


async def test_falha_de_disco_vira_mensagem_e_nao_500(tmp_path, monkeypatch):
    """Volume de uploads não montado/sem permissão: antes estourava 500 seco (e, sem
    CORS na resposta de erro, o navegador só dizia "bloqueado por CORS")."""
    from aiworkspace import uploads_service as svc

    arquivo = tmp_path / "isto-e-um-arquivo"
    arquivo.write_text("x", encoding="utf-8")
    monkeypatch.setattr(svc, "root", lambda: arquivo / "uploads")   # pasta impossível

    async def _pedacos():
        yield b"conteudo"

    with pytest.raises(svc.UploadStorageError) as erro:
        await svc.store_stream(None, uuid.uuid4(), "nota.txt", "text/plain", _pedacos())
    assert erro.value.status_code == 507
    assert "volume de uploads" in erro.value.message
