"""WhatsApp por QR sem a Evolution: o whatsmeow embutido (neonize) no app desktop.

A fachada `whatsapp_qr` escolhe o motor da instalação; o motor local entrega as
mensagens recebidas no MESMO formato do webhook da Evolution, para o resto do
atendimento (filtros, threads, Audio Router) não saber qual motor rodou.
"""
from __future__ import annotations

import asyncio

import pytest

from aiworkspace.config import get_settings
from aiworkspace.integrations import whatsapp_evolution, whatsapp_local, whatsapp_qr


# --------------------------------------------------------------------------- #
# Escolha do motor                                                              #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("modo,evolution,local,esperado", [
    ("auto", True, True, "evolution"),     # Docker com a Evolution configurada
    ("auto", False, True, "local"),        # desktop: sem Evolution, com neonize
    ("auto", False, False, ""),            # nada disponível → a UI mostra o motivo
    ("local", True, True, "local"),
    ("local", True, False, ""),            # pediu local sem o neonize instalado
    ("evolution", False, True, ""),        # pediu Evolution sem configurar
])
def test_escolha_do_motor(monkeypatch, modo, evolution, local, esperado):
    monkeypatch.setattr(get_settings(), "whatsapp_qr_backend", modo, raising=False)
    monkeypatch.setattr(whatsapp_evolution, "configured", lambda: evolution)
    monkeypatch.setattr(whatsapp_local, "available", lambda: local)

    assert whatsapp_qr.backend() == esperado
    assert whatsapp_qr.configured() == bool(esperado)
    if not esperado:
        assert whatsapp_qr.unavailable_reason()


def test_chamadas_vao_para_o_motor_escolhido(monkeypatch):
    monkeypatch.setattr(get_settings(), "whatsapp_qr_backend", "local", raising=False)
    monkeypatch.setattr(whatsapp_local, "available", lambda: True)
    chamadas = []

    async def enviar(instance, jid, text, delay_ms=0):
        chamadas.append((instance, jid, text))
        return {}

    monkeypatch.setattr(whatsapp_local, "send_text", enviar)

    asyncio.run(whatsapp_qr.send_text("aw1", "5511999999999", "oi"))

    assert chamadas == [("aw1", "5511999999999", "oi")]


# --------------------------------------------------------------------------- #
# Sessões órfãs                                                                  #
# --------------------------------------------------------------------------- #
def test_sessao_de_conexao_excluida_sai_no_proximo_inicio(monkeypatch, tmp_path):
    """Durante o pareamento o neonize segura o arquivo da sessão até o processo sair:
    a conexão excluída deixa o arquivo, e o próximo início o remove."""
    monkeypatch.setattr(get_settings(), "whatsapp_local_dir", str(tmp_path), raising=False)
    (tmp_path / "awviva.db").write_bytes(b"x")
    (tmp_path / "awexcluida.db").write_bytes(b"x")
    (tmp_path / "awexcluida.db-wal").write_bytes(b"x")

    asyncio.run(whatsapp_local._cleanup_orphans({"awviva"}))

    assert sorted(p.name for p in tmp_path.iterdir()) == ["awviva.db"]


# --------------------------------------------------------------------------- #
# Normalização da mensagem recebida (precisa do neonize)                        #
# --------------------------------------------------------------------------- #
def _ev(chat="5511999990000@s.whatsapp.net", sender=None, sender_alt="", texto="oi",
        audio=0, grupo=False, de_mim=False, ts=1_790_000_000, msg_id="ABC", nome="Ana"):
    pytest.importorskip("neonize")
    from neonize.events import MessageEv
    from neonize.utils.jid import build_jid

    def jid(s):
        u, srv = s.split("@")
        return build_jid(u, srv)

    ev = MessageEv()
    src = ev.Info.MessageSource
    src.Chat.CopyFrom(jid(chat))
    src.Sender.CopyFrom(jid(sender or chat))
    if sender_alt:
        src.SenderAlt.CopyFrom(jid(sender_alt))
    src.IsGroup = grupo
    src.IsFromMe = de_mim
    ev.Info.ID = msg_id
    ev.Info.Pushname = nome
    ev.Info.Timestamp = ts
    if texto:
        ev.Message.conversation = texto
    if audio:
        ev.Message.audioMessage.seconds = audio
    return ev


def test_texto_vira_o_formato_do_webhook_da_evolution():
    m = whatsapp_local.normalize(_ev())

    assert m == {
        "jid": "5511999990000@s.whatsapp.net", "sender": "5511999990000@s.whatsapp.net",
        "text": "oi", "sender_name": "Ana", "from_me": False, "is_group": False,
        "msg_id": "ABC", "has_audio": False, "audio_seconds": 0, "ts": 1_790_000_000,
    }


def test_remetente_em_lid_usa_o_numero_real_para_os_filtros():
    """O WhatsApp identifica muitas conversas por LID; os filtros por contato comparam
    números. A resposta vai para o Chat como veio; o `sender` usa o número real."""
    m = whatsapp_local.normalize(_ev(chat="123456789@lid", sender="123456789@lid",
                                     sender_alt="5511988887777@s.whatsapp.net"))

    assert m["jid"] == "123456789@lid"
    assert m["sender"] == "5511988887777@s.whatsapp.net"


def test_grupo_e_audio():
    m = whatsapp_local.normalize(_ev(chat="120363@g.us", sender="5511977776666@s.whatsapp.net",
                                     texto="", audio=7, grupo=True))

    assert m["is_group"] and m["has_audio"] and m["audio_seconds"] == 7
    assert m["sender"] == "5511977776666@s.whatsapp.net"


@pytest.mark.parametrize("kwargs", [
    {"chat": "status@broadcast"},          # status do WhatsApp
    {"texto": "", "audio": 0},             # sem texto nem áudio (figurinha, reação…)
])
def test_o_que_nao_se_responde_e_ignorado(kwargs):
    assert whatsapp_local.normalize(_ev(**kwargs)) is None


def test_timestamp_em_milissegundos_vira_segundos():
    """O filtro de mensagem velha (histórico reenviado na reconexão) compara segundos."""
    assert whatsapp_local.normalize(_ev(ts=1_790_000_000_123))["ts"] == 1_790_000_000
