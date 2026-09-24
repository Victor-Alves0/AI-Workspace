"""WhatsApp por QR Code SEM serviço externo — o whatsmeow (Go) dentro do processo.

O caminho não oficial do Docker é a Evolution API: um serviço Node com banco próprio,
centenas de MB de dependências e um webhook de volta para o server. No app desktop
(Windows, sem Docker) isso não cabe. Aqui o mesmo papel é feito pelo whatsmeow — a
biblioteca Go que implementa o WhatsApp Web multi-dispositivo — embutido via
`neonize` (~25 MB, com a DLL já compilada para Windows e Linux).

Mesma interface do `whatsapp_evolution` (a fachada `whatsapp_qr` escolhe um ou outro),
com três diferenças de natureza:
  - sem webhook: as mensagens recebidas vão direto para `whatsapp_service.handle_incoming`
    (a conexão é achada pelo nome da instância, que é único);
  - o server religa as sessões salvas ao subir (`resume_all`) — a Evolution é um serviço
    à parte e se religava sozinha;
  - o whatsmeow não guarda histórico: o app guarda (`whatsapp_history`, no Postgres)
    tudo o que passa pela sessão, mais o histórico que o WhatsApp manda ao parear.

Aviso honesto (o mesmo da Evolution): integração não oficial viola os termos do
WhatsApp e pode banir o número — a UI avisa; use um número secundário.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from ..config import get_settings
from . import whatsapp_history

logger = logging.getLogger(__name__)

_MEDIA_CACHE = 300         # mídias recentes em memória (o banco é a fonte; isto é atalho)
_HIST_PER_CHAT = 300       # mensagens importadas por conversa no histórico do pareamento
_MEDIA_KINDS = {"audio", "image", "video", "document", "sticker"}
_QR_WAIT = 12.0            # espera pelo 1º QR ao pedir um


def available() -> bool:
    """O neonize está instalado? (vai no motor do desktop; não na imagem Docker)."""
    try:
        import neonize  # noqa: F401
    except Exception:  # noqa: BLE001 - ausente ou DLL que não carrega
        return False
    return True


def _data_dir() -> Path:
    s = get_settings()
    base = (s.whatsapp_local_dir or "").strip()
    pasta = Path(base) if base else Path(s.uploads_dir).parent / "whatsapp"
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def _db_path(instance: str) -> Path:
    return _data_dir() / f"{instance}.db"


# --------------------------------------------------------------------------- #
# Normalização (pura — testável sem celular)                                   #
# --------------------------------------------------------------------------- #
def _jid_str(jid: Any) -> str:
    if jid is None or not getattr(jid, "User", "") and not getattr(jid, "Server", ""):
        return ""
    user, server = getattr(jid, "User", ""), getattr(jid, "Server", "")
    return f"{user}@{server}" if user else server


def _ts(value: Any) -> int:
    """Timestamp do whatsmeow em segundos (se vier em ms, converte)."""
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return n // 1000 if n > 10**12 else n


def _text_of(msg: Any) -> str:
    if msg is None:
        return ""
    for caminho in (("conversation",), ("extendedTextMessage", "text"),
                    ("imageMessage", "caption"), ("videoMessage", "caption"),
                    ("documentMessage", "caption")):
        alvo = msg
        for campo in caminho:
            alvo = getattr(alvo, campo, None)
            if alvo is None:
                break
        if isinstance(alvo, str) and alvo.strip():
            return alvo.strip()
    return ""


def describe(msg: Any) -> tuple[str, str]:
    """(texto, tipo) de uma mensagem: tipo = text|audio|image|video|document|sticker."""
    text = _text_of(msg)
    tem = getattr(msg, "HasField", None)
    if msg is not None and tem is not None:
        for campo, tipo in (("audioMessage", "audio"), ("imageMessage", "image"),
                            ("videoMessage", "video"), ("documentMessage", "document"),
                            ("stickerMessage", "sticker")):
            try:
                if tem(campo):
                    return text, tipo
            except ValueError:
                continue
    return text, "text"


def _row(jid: str, msg: Any, *, msg_id: str, from_me: bool, sender: str,
         sender_name: str, ts: Any) -> dict[str, Any] | None:
    """Linha do histórico; None quando não há o que guardar (reação, aviso de sistema…)."""
    if not jid or not msg_id or jid.endswith(("@broadcast", "@newsletter")):
        return None
    text, kind = describe(msg)
    if not text and kind == "text":
        return None
    media = None
    if kind in _MEDIA_KINDS:
        try:
            media = msg.SerializeToString()
        except Exception:  # noqa: BLE001
            media = None
    return {"jid": jid, "msg_id": msg_id, "from_me": from_me, "sender": sender,
            "sender_name": sender_name, "text": text, "kind": kind, "ts": _ts(ts), "media": media}


def record_of(ev: Any) -> dict[str, Any] | None:
    """MessageEv → linha do histórico (inclui mídia sem legenda e o que VOCÊ mandou)."""
    info = getattr(ev, "Info", None)
    src = getattr(info, "MessageSource", None)
    if info is None or src is None:
        return None
    return _row(_jid_str(getattr(src, "Chat", None)), getattr(ev, "Message", None),
                msg_id=getattr(info, "ID", "") or "", from_me=bool(getattr(src, "IsFromMe", False)),
                sender=_jid_str(getattr(src, "Sender", None)),
                sender_name=getattr(info, "Pushname", "") or "", ts=getattr(info, "Timestamp", 0))


def history_rows(data: Any) -> tuple[list[dict[str, Any]], dict[str, tuple[str, bool]]]:
    """HistorySync (enviado ao parear) → (linhas, {jid: (nome, é_grupo)})."""
    rows: list[dict[str, Any]] = []
    names: dict[str, tuple[str, bool]] = {}
    apelidos = {p.ID: p.pushname for p in getattr(data, "pushnames", []) if p.ID and p.pushname}
    for conv in getattr(data, "conversations", []):
        jid = conv.ID
        if not jid or jid.endswith(("@broadcast", "@newsletter")):
            continue
        grupo = jid.endswith("@g.us")
        nome = conv.name or conv.displayName or ("" if grupo else apelidos.get(jid, ""))
        names[jid] = (nome, grupo)
        for hm in list(conv.messages)[-_HIST_PER_CHAT:]:
            w = hm.message
            k = w.key
            remetente = (k.participant or w.participant) if grupo else ("" if k.fromMe else jid)
            linha = _row(jid, w.message, msg_id=k.ID, from_me=bool(k.fromMe), sender=remetente,
                         sender_name=w.pushName or "", ts=w.messageTimestamp)
            if linha is not None:
                rows.append(linha)
    return rows, names


def normalize(ev: Any) -> dict[str, Any] | None:
    """MessageEv do whatsmeow → o formato do `whatsapp_evolution.parse_webhook`:
    {jid, sender, text, sender_name, from_me, is_group, msg_id, has_audio,
    audio_seconds, ts}. None quando não há o que responder (sem texto nem áudio).

    O WhatsApp identifica muitas conversas por LID (`...@lid`) em vez do número. A
    resposta vai para o `Chat` como veio (o whatsmeow entrega em LID), mas o `sender`
    — que os filtros por contato comparam com números — usa o número real
    (`SenderAlt`) quando o remetente está em LID."""
    info = getattr(ev, "Info", None)
    src = getattr(info, "MessageSource", None)
    if info is None or src is None:
        return None
    jid = _jid_str(getattr(src, "Chat", None))
    if not jid or jid.endswith("@broadcast") or jid.endswith("@newsletter"):
        return None
    msg = getattr(ev, "Message", None)
    text = _text_of(msg)
    audio = getattr(msg, "audioMessage", None) if msg is not None else None
    tem_audio = bool(msg is not None and msg.HasField("audioMessage")) if hasattr(msg, "HasField") else False
    if not text and not tem_audio:
        return None
    sender = getattr(src, "Sender", None)
    alt = getattr(src, "SenderAlt", None)
    remetente = _jid_str(sender)
    if remetente.endswith("@lid") and _jid_str(alt):
        remetente = _jid_str(alt)
    return {
        "jid": jid,
        "sender": remetente or jid,
        "text": text,
        "sender_name": getattr(info, "Pushname", "") or "",
        "from_me": bool(getattr(src, "IsFromMe", False)),
        "is_group": bool(getattr(src, "IsGroup", False)) or jid.endswith("@g.us"),
        "msg_id": getattr(info, "ID", "") or "",
        "has_audio": tem_audio,
        "audio_seconds": int(getattr(audio, "seconds", 0) or 0) if tem_audio else 0,
        "ts": _ts(getattr(info, "Timestamp", 0)),
    }


def _to_jid(value: str):
    """'5511999999999', '5511999999999@s.whatsapp.net', '123@lid', '...@g.us' → JID."""
    from neonize.utils.jid import build_jid

    v = (value or "").strip()
    if "@" in v:
        user, server = v.split("@", 1)
        return build_jid(user.split(":")[0], server)
    return build_jid("".join(c for c in v if c.isdigit()))


def _qr_data_url(code: str) -> str:
    import qrcode

    img = qrcode.make(code)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# --------------------------------------------------------------------------- #
# Sessões                                                                        #
# --------------------------------------------------------------------------- #
class _Session:
    def __init__(self, instance: str) -> None:
        self.instance = instance
        self.client: Any = None
        self.state = "connecting"      # open | connecting | close
        self.qr = ""
        self.qr_event = asyncio.Event()
        self.media: OrderedDict[str, Any] = OrderedDict()


_SESSIONS: dict[str, _Session] = {}
_LOCK = asyncio.Lock()


async def _set_state(instance: str, state: str) -> None:
    """Espelha o estado na conexão (o que o webhook CONNECTION_UPDATE fazia)."""
    from sqlalchemy import select

    from ..db import SessionLocal
    from ..models import WhatsAppConnection

    try:
        async with SessionLocal() as db:
            conn = await db.scalar(select(WhatsAppConnection).where(WhatsAppConnection.instance == instance))
            if conn is not None:
                conn.state = {**(conn.state or {}), "status": state}
                await db.commit()
    except Exception as exc:  # noqa: BLE001 - estado é informativo
        logger.debug("whatsapp local: não gravou o estado de %s: %s", instance, exc)


async def _deliver(instance: str, m: dict[str, Any]) -> None:
    """Mensagem recebida → o mesmo caminho do webhook da Evolution."""
    from sqlalchemy import select

    from .. import bg
    from ..db import SessionLocal
    from ..models import WhatsAppConnection
    from . import whatsapp_service

    async with SessionLocal() as db:
        conn = await db.scalar(select(WhatsAppConnection).where(WhatsAppConnection.instance == instance))
    if conn is None:
        return
    bg.spawn(whatsapp_service.handle_incoming(conn.id, [m]))


async def _start(instance: str) -> _Session:
    async with _LOCK:
        sess = _SESSIONS.get(instance)
        if sess is not None and sess.client is not None:
            return sess
        from neonize.aioze.client import NewAClient
        from neonize.events import ConnectedEv, HistorySyncEv, LoggedOutEv, MessageEv, PairStatusEv

        logging.getLogger("whatsmeow").setLevel(logging.WARNING)
        sess = sess or _Session(instance)
        client = NewAClient(str(_db_path(instance)), uuid=instance)

        async def on_qr(_c, data: bytes) -> None:
            sess.qr = data.decode() if isinstance(data, (bytes, bytearray)) else str(data)
            sess.state = "connecting"
            sess.qr_event.set()

        async def on_connected(_c, _ev) -> None:
            sess.state, sess.qr = "open", ""
            await _set_state(instance, "open")

        async def on_pair(_c, ev) -> None:
            if getattr(ev, "Error", ""):
                logger.warning("whatsapp local: pareamento falhou (%s): %s", instance, ev.Error)

        async def on_logout(_c, _ev) -> None:
            sess.state = "close"
            await _set_state(instance, "close")

        async def on_message(_c, ev) -> None:
            linha = record_of(ev)
            if linha is not None:
                if linha["media"] is not None:
                    sess.media[linha["msg_id"]] = ev.Message
                    while len(sess.media) > _MEDIA_CACHE:
                        sess.media.popitem(last=False)
                await whatsapp_history.record(instance, [linha])
            m = normalize(ev)
            if m is not None and not m["from_me"]:
                await _deliver(instance, m)

        async def on_history(_c, ev) -> None:
            linhas, nomes = history_rows(ev.Data)
            n = await whatsapp_history.record(instance, linhas, nomes)
            logger.info("whatsapp local: histórico de %s: %d conversa(s), %d mensagem(ns) nova(s)",
                        instance, len(nomes), n)

        client.qr(on_qr)
        client.event(ConnectedEv)(on_connected)
        client.event(PairStatusEv)(on_pair)
        client.event(LoggedOutEv)(on_logout)
        client.event(MessageEv)(on_message)
        client.event(HistorySyncEv)(on_history)
        await client.connect()
        sess.client = client
        _SESSIONS[instance] = sess
        return sess


async def _session(instance: str) -> _Session:
    sess = _SESSIONS.get(instance)
    if sess is not None and sess.client is not None:
        return sess
    return await _start(instance)


def _client_or_fail(sess: _Session):
    if sess.state != "open":
        raise RuntimeError("o WhatsApp desta conexão não está conectado (escaneie o QR Code)")
    return sess.client


async def resume_all() -> None:
    """Religa, ao subir o server, as conexões que já têm sessão salva."""
    from sqlalchemy import select

    from ..db import SessionLocal
    from ..models import WhatsAppConnection

    async with SessionLocal() as db:
        rows = list(await db.scalars(select(WhatsAppConnection).where(
            WhatsAppConnection.provider == "evolution", WhatsAppConnection.instance.isnot(None))))
    await _cleanup_orphans({c.instance for c in rows if c.instance})
    for conn in rows:
        if conn.instance and _db_path(conn.instance).exists():
            try:
                await _start(conn.instance)
            except Exception as exc:  # noqa: BLE001 - uma sessão ruim não impede as outras
                logger.warning("whatsapp local: não religou %s: %s", conn.instance, exc)


async def _stop(sess: _Session) -> None:
    """Pede o fim da sessão ao lado Go (`stop`) e espera um pouco.

    Limitação do neonize, medida: durante o pareamento (QR na tela) nem `stop` nem
    `StopAll` devolvem a chamada Go que segura a conexão — a thread dela fica viva até
    o processo sair, e o arquivo da sessão fica aberto. Por isso: o arquivo de uma
    conexão excluída é apagado no PRÓXIMO boot (`_cleanup_orphans`), e o desligamento
    não depende disso — o launcher do desktop e o Tauri encerram o motor à força."""
    client = sess.client
    if client is None:
        return
    try:
        await client.stop()
    except Exception as exc:  # noqa: BLE001
        logger.debug("whatsapp local: stop de %s falhou: %s", sess.instance, exc)
    tarefa = getattr(client, "connect_task", None)
    if tarefa is not None:
        try:
            await asyncio.wait_for(asyncio.shield(tarefa), 3)
        except Exception:  # noqa: BLE001 - ver a limitação acima
            pass
    sess.client = None


async def shutdown() -> None:
    for sess in list(_SESSIONS.values()):
        await _stop(sess)
    _SESSIONS.clear()


# --------------------------------------------------------------------------- #
# Interface (a mesma do whatsapp_evolution)                                     #
# --------------------------------------------------------------------------- #
async def create_instance(instance: str, webhook_url: str) -> dict[str, Any]:  # noqa: ARG001
    """Cria a sessão e começa a conectar (o QR sai em seguida). Sem webhook: as
    mensagens chegam direto ao `whatsapp_service`."""
    await _start(instance)
    return {"instance": {"instanceName": instance, "status": "connecting"}}


async def get_qr(instance: str) -> dict[str, Any]:
    sess = await _session(instance)
    if sess.state == "open":
        return {"instance": {"state": "open"}}
    if not sess.qr:
        try:
            await asyncio.wait_for(sess.qr_event.wait(), _QR_WAIT)
        except asyncio.TimeoutError:
            return {}
    return {"base64": _qr_data_url(sess.qr), "code": sess.qr} if sess.qr else {}


async def get_state(instance: str) -> str:
    sess = _SESSIONS.get(instance)
    if sess is None:
        if not _db_path(instance).exists():
            return "close"
        sess = await _start(instance)  # server reiniciou: religa ao consultar
    return sess.state


async def get_profile(instance: str) -> dict[str, str]:
    try:
        sess = await _session(instance)
        me = await sess.client.get_me()
        return {"phone": me.JID.User or "", "name": me.PushName or ""}
    except Exception as exc:  # noqa: BLE001
        logger.debug("whatsapp local: perfil de %s indisponível: %s", instance, exc)
        return {"phone": "", "name": ""}


async def send_text(instance: str, jid: str, text: str, delay_ms: int = 0) -> dict[str, Any]:
    sess = await _session(instance)
    client = _client_or_fail(sess)
    to = _to_jid(jid)
    if delay_ms > 0:
        await send_presence(instance, jid, "composing", delay_ms)
        await asyncio.sleep(delay_ms / 1000)
    r = await client.send_message(to, text)
    await whatsapp_history.record(instance, [{
        "jid": jid, "msg_id": getattr(r, "ID", ""), "from_me": True, "text": text,
        "kind": "text", "ts": int(time.time())}])
    return {"key": {"id": getattr(r, "ID", "")}}


async def send_media(instance: str, jid: str, data: bytes, mime: str,
                     filename: str, caption: str = "") -> dict[str, Any]:
    sess = await _session(instance)
    client = _client_or_fail(sess)
    to = _to_jid(jid)
    mime = mime or "application/octet-stream"
    if mime.startswith("image/"):
        r = await client.send_image(to, data, caption=caption or None)
    elif mime.startswith("video/"):
        r = await client.send_video(to, data, caption=caption or None)
    elif mime.startswith("audio/"):
        r = await client.send_audio(to, data)
    else:
        r = await client.send_document(to, data, caption=caption or None,
                                       filename=filename, mimetype=mime)
    tipo = next((t for t in ("image", "video", "audio") if mime.startswith(t + "/")), "document")
    await whatsapp_history.record(instance, [{
        "jid": jid, "msg_id": getattr(r, "ID", ""), "from_me": True,
        "text": caption or (filename if tipo == "document" else ""), "kind": tipo, "ts": int(time.time())}])
    return {"key": {"id": getattr(r, "ID", "")}}


async def send_presence(instance: str, jid: str, presence: str = "composing", delay_ms: int = 3000) -> None:  # noqa: ARG001
    """'composing' (digitando), 'recording' (gravando) ou 'paused'. Best-effort."""
    try:
        from neonize.utils.enum import ChatPresence, ChatPresenceMedia

        sess = await _session(instance)
        client = _client_or_fail(sess)
        estado = ChatPresence.CHAT_PRESENCE_PAUSED if presence == "paused" else ChatPresence.CHAT_PRESENCE_COMPOSING
        midia = ChatPresenceMedia.CHAT_PRESENCE_MEDIA_AUDIO if presence == "recording" \
            else ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT
        await client.send_chat_presence(_to_jid(jid), estado, midia)
    except Exception as exc:  # noqa: BLE001
        logger.debug("whatsapp local: presença falhou (%s): %s", instance, exc)


async def delete_instance(instance: str) -> None:
    """Desconecta, desvincula o aparelho e apaga a sessão (ao excluir a conexão)."""
    whatsapp_history.forget(instance)
    sess = _SESSIONS.pop(instance, None)
    if sess is not None and sess.client is not None:
        if sess.state == "open":
            try:
                await sess.client.logout()  # desvincula o aparelho no celular
            except Exception as exc:  # noqa: BLE001
                logger.warning("whatsapp local: logout de %s falhou: %s", instance, exc)
        await _stop(sess)
    # o logout já invalidou a sessão; se o arquivo seguir aberto pelo lado Go, ele sai
    # no próximo boot (_cleanup_orphans) — antes de qualquer sessão abrir
    if not _remove_files(instance):
        logger.info("whatsapp local: a sessão de %s será apagada no próximo início", instance)


def _remove_files(instance: str) -> bool:
    """Apaga o banco da sessão (e os arquivos auxiliares do SQLite). True = sumiu."""
    for alvo in (Path(str(_db_path(instance)) + x) for x in ("", "-shm", "-wal", "-journal")):
        try:
            alvo.unlink(missing_ok=True)
        except OSError:
            pass
    return not _db_path(instance).exists()


async def _cleanup_orphans(conhecidas: set[str]) -> None:
    """Sessões cujo arquivo ficou para trás (conexão excluída com a sessão aberta)."""
    for arq in _data_dir().glob("*.db"):
        if arq.stem not in conhecidas and arq.stem not in _SESSIONS:
            if _remove_files(arq.stem):
                logger.info("whatsapp local: sessão órfã %s apagada", arq.stem)


async def get_media_base64(instance: str, msg_id: str) -> tuple[str, str]:
    sess = await _session(instance)
    msg = sess.media.get(msg_id)
    if msg is None:
        guardada = await whatsapp_history.media(instance, msg_id)
        if guardada is None:
            raise RuntimeError("a mídia desta mensagem não está disponível")
        from neonize.events import MessageEv

        msg = type(MessageEv().Message)()
        msg.ParseFromString(guardada)
    dados = await sess.client.download_any(msg)
    mime = "audio/ogg"
    for campo in ("audioMessage", "imageMessage", "videoMessage", "documentMessage"):
        if msg.HasField(campo):
            mime = getattr(msg, campo).mimetype or mime
            break
    return base64.b64encode(dados).decode(), mime.split(";")[0]


async def find_chats(instance: str, limit: int = 50) -> list[dict[str, str]]:
    """Conversas do número (histórico gravado), mais as que já viraram chat no app."""
    await _name_groups(instance)
    out: dict[str, dict[str, Any]] = {c["jid"]: c for c in await whatsapp_history.chats(instance, limit)}
    for t in await _threads(instance):
        c = out.setdefault(t.jid, {"jid": t.jid, "name": "", "is_group": t.jid.endswith("@g.us")})
        c["name"] = c["name"] or t.contact_name or ""
    return list(out.values())[: max(1, limit)]


async def _name_groups(instance: str, maximo: int = 10) -> None:
    """Grupos sem nome (vieram só de mensagens): pergunta o nome ao WhatsApp."""
    sess = _SESSIONS.get(instance)
    if sess is None or sess.state != "open":
        return
    nomes: dict[str, tuple[str, bool]] = {}
    for jid in (await whatsapp_history.unnamed_groups(instance))[:maximo]:
        try:
            info = await sess.client.get_group_info(_to_jid(jid))
            nome = getattr(getattr(info, "GroupName", None), "Name", "") or ""
        except Exception as exc:  # noqa: BLE001
            logger.debug("whatsapp local: nome do grupo %s indisponível: %s", jid, exc)
            continue
        if nome:
            nomes[jid] = (nome, True)
    if nomes:
        await whatsapp_history.record(instance, [], nomes)


async def find_contacts(instance: str, query: str = "", limit: int = 30) -> list[dict[str, str]]:
    sess = await _session(instance)
    try:
        contatos = await _client_or_fail(sess).contact.get_all_contacts()
    except Exception as exc:  # noqa: BLE001
        logger.debug("whatsapp local: contatos indisponíveis: %s", exc)
        return []
    q = (query or "").strip().lower()
    out: list[dict[str, str]] = []
    for c in contatos:
        jid = _jid_str(c.JID)
        info = c.Info
        nome = info.FullName or info.FirstName or info.PushName or info.BusinessName or ""
        if not jid or jid.endswith("@g.us"):
            continue
        if q and q not in nome.lower() and q not in jid.lower():
            continue
        out.append({"jid": jid, "name": nome, "is_group": False})
        if len(out) >= max(1, limit):
            break
    return out


async def find_messages(instance: str, jid: str, limit: int = 20) -> list[dict[str, Any]]:
    """Últimas mensagens da conversa, do histórico gravado. Conversas anteriores ao
    histórico (sem nenhuma linha) caem no que já foi gravado no Chat da conversa."""
    limite = int(max(1, min(limit, 100)))
    return await whatsapp_history.messages(instance, jid, limite) or \
        await _history_from_chat(instance, jid, limite)


async def _threads(instance: str):
    from sqlalchemy import select

    from ..db import SessionLocal
    from ..models import WhatsAppConnection, WhatsAppThread

    async with SessionLocal() as db:
        conn = await db.scalar(select(WhatsAppConnection).where(WhatsAppConnection.instance == instance))
        if conn is None:
            return []
        return list(await db.scalars(select(WhatsAppThread).where(WhatsAppThread.connection_id == conn.id)))


async def _history_from_chat(instance: str, jid: str, limite: int) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from ..db import SessionLocal
    from ..models import Message

    thread = next((t for t in await _threads(instance) if t.jid == jid), None)
    if thread is None:
        return []
    async with SessionLocal() as db:
        rows = list(await db.scalars(
            select(Message).where(Message.chat_id == thread.chat_id, Message.role.in_(("user", "assistant")))
            .order_by(Message.created_at.desc()).limit(limite)
        ))
    return [{"from_me": m.role == "assistant", "text": m.content or "",
             "sender_name": "" if m.role == "assistant" else (thread.contact_name or ""),
             "ts": int(m.created_at.timestamp()) if m.created_at else 0, "msg_id": ""}
            for m in reversed(rows)]

