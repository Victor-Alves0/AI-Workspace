"""Histórico do WhatsApp embutido: evento/sincronização → linhas (puro, sem celular)."""
from __future__ import annotations

from types import SimpleNamespace as NS

from aiworkspace.integrations import whatsapp_local as wl


class _Msg:
    """Imita a Message protobuf: campos + HasField + SerializeToString."""

    def __init__(self, **campos):
        self.__dict__.update(campos)

    def HasField(self, nome):  # noqa: N802 - API do protobuf
        return nome in self.__dict__

    def SerializeToString(self):  # noqa: N802
        return b"proto:" + ",".join(sorted(self.__dict__)).encode()


def _jid(user, server="s.whatsapp.net"):
    return NS(User=user, Server=server)


def _ev(msg, *, chat="5511999@s.whatsapp.net", from_me=False, msg_id="M1"):
    user, server = chat.split("@")
    return NS(Info=NS(MessageSource=NS(Chat=_jid(user, server), Sender=_jid("5511999"), SenderAlt=None,
                                       IsFromMe=from_me, IsGroup=server == "g.us"),
                      ID=msg_id, Pushname="Ana", Timestamp=1_700_000_000), Message=msg)


def test_texto_vira_linha_sem_midia():
    linha = wl.record_of(_ev(_Msg(conversation="oi")))
    assert linha["text"] == "oi" and linha["kind"] == "text" and linha["media"] is None
    assert linha["jid"] == "5511999@s.whatsapp.net" and linha["sender_name"] == "Ana"


def test_imagem_sem_legenda_entra_com_a_midia_serializada():
    linha = wl.record_of(_ev(_Msg(imageMessage=NS(caption=""))))
    assert linha["kind"] == "image" and linha["text"] == ""
    assert linha["media"].startswith(b"proto:")


def test_o_que_voce_mandou_do_celular_tambem_entra():
    linha = wl.record_of(_ev(_Msg(conversation="já vou"), from_me=True))
    assert linha["from_me"] is True


def test_reacao_e_aviso_de_sistema_nao_entram():
    assert wl.record_of(_ev(_Msg(reactionMessage=NS(text="👍")))) is None
    assert wl.record_of(_ev(_Msg(conversation="x"), chat="status@broadcast")) is None


def _wmi(msg_id, texto, *, from_me=False, participant="", push=""):
    return NS(message=NS(key=NS(ID=msg_id, fromMe=from_me, participant=participant, remoteJID=""),
                         message=_Msg(conversation=texto), participant="", pushName=push,
                         messageTimestamp=1_700_000_100))


def test_historico_do_pareamento_traz_conversas_nomes_e_remetentes():
    data = NS(
        pushnames=[NS(ID="5511888@s.whatsapp.net", pushname="Beto")],
        conversations=[
            NS(ID="5511888@s.whatsapp.net", name="", displayName="",
               messages=[_wmi("a1", "e aí"), _wmi("a2", "tudo", from_me=True)]),
            NS(ID="123-456@g.us", name="Família", displayName="",
               messages=[_wmi("g1", "bom dia", participant="5511777@s.whatsapp.net", push="Tia")]),
            NS(ID="status@broadcast", name="", displayName="", messages=[_wmi("s1", "x")]),
        ],
    )
    linhas, nomes = wl.history_rows(data)
    assert nomes == {"5511888@s.whatsapp.net": ("Beto", False), "123-456@g.us": ("Família", True)}
    por_id = {r["msg_id"]: r for r in linhas}
    assert set(por_id) == {"a1", "a2", "g1"}
    assert por_id["a1"]["sender"] == "5511888@s.whatsapp.net" and not por_id["a1"]["from_me"]
    assert por_id["a2"]["from_me"] and por_id["a2"]["sender"] == ""
    assert por_id["g1"]["sender"] == "5511777@s.whatsapp.net" and por_id["g1"]["sender_name"] == "Tia"


def test_historico_limita_mensagens_por_conversa(monkeypatch):
    monkeypatch.setattr(wl, "_HIST_PER_CHAT", 2)
    data = NS(pushnames=[], conversations=[NS(ID="1@s.whatsapp.net", name="x", displayName="",
                                              messages=[_wmi(f"m{i}", "t") for i in range(5)])])
    linhas, _ = wl.history_rows(data)
    assert [r["msg_id"] for r in linhas] == ["m3", "m4"]  # as mais recentes
