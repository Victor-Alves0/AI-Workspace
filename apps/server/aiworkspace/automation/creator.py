"""Criação de monitores a partir da system tool `automation.monitor.create`.

A tool SIFT roda de forma síncrona num thread do pool (dispatch), então usamos
`asyncio.run(create_monitor(...))`. Para não colidir com o event loop principal
(o pool de conexões async é loop-bound), abrimos um engine efêmero (NullPool)
próprio desta chamada. Escrita via ORM p/ respeitar o EncryptedText do modelo.

Isolado de propósito: NÃO importa scheduler/runner/sift_service (evita ciclo de
import). O `next_run_at` é calculado inline.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ..config import get_settings
from ..models import Automation

_MIN_INTERVAL = 60
_TYPES = ("price", "web_search", "page", "rss")
_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _watcher_config(watcher_type: str, f: dict) -> tuple[dict, str | None]:
    """Monta o watcher_config a partir dos campos achatados da tool + valida."""
    if watcher_type == "price":
        symbol = (f.get("symbol") or "").strip()
        if not symbol:
            return {}, "price monitor requires `symbol` (e.g. PETR4.SA, AAPL)"
        if f.get("value") in (None, ""):
            return {}, "price monitor requires `value` (the threshold or percent)"
        op = (f.get("op") or "above").lower()
        if op not in ("above", "below", "pct"):
            op = "above"
        return {"symbol": symbol, "op": op, "value": f.get("value")}, None
    if watcher_type == "web_search":
        query = (f.get("query") or "").strip()
        if not query:
            return {}, "web_search monitor requires `query`"
        cfg: dict = {"query": query}
        if (f.get("condition") or "").strip():
            cfg["condition"] = f["condition"].strip()
        return cfg, None
    if watcher_type == "page":
        url = (f.get("url") or "").strip()
        if not url:
            return {}, "page monitor requires `url`"
        cfg = {"url": url}
        if (f.get("contains") or "").strip():
            cfg["contains"] = f["contains"].strip()
        return cfg, None
    if watcher_type == "rss":
        url = (f.get("url") or "").strip()
        if not url:
            return {}, "rss monitor requires `url`"
        cfg = {"url": url}
        kw = f.get("keywords")
        if isinstance(kw, str):
            kw = [k.strip() for k in kw.split(",") if k.strip()]
        if kw:
            cfg["keywords"] = list(kw)
        return cfg, None
    return {}, f"unknown watcher_type '{watcher_type}' (use: {', '.join(_TYPES)})"


async def create_monitor(
    user_id, *, title: str = "", watcher_type: str = "price",
    interval_minutes: float = 5, instructions: str = "", **fields,
) -> dict:
    wt = (watcher_type or "").strip().lower()
    if wt not in _TYPES:
        return {"error": f"unknown watcher_type '{wt}' (use: {', '.join(_TYPES)})"}
    cfg, err = _watcher_config(wt, fields)
    if err:
        return {"error": err}
    try:
        interval = max(int(float(interval_minutes or 5)) * 60, _MIN_INTERVAL)
    except (TypeError, ValueError):
        interval = 300

    try:
        uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    except (ValueError, TypeError):
        return {"error": "invalid user"}

    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            a = Automation(
                user_id=uid,
                title=(title or f"Monitor: {wt}")[:255],
                kind="monitor",
                enabled=True,
                model="",
                instructions=instructions or "",
                watcher_type=wt,
                watcher_config=cfg,
                interval_seconds=interval,
                target={"mode": "reuse"},
                # dispara logo no próximo tick p/ estabelecer a linha de base
                next_run_at=datetime.now(timezone.utc),
            )
            db.add(a)
            await db.commit()
            await db.refresh(a)
            return {"ok": True, "monitor_id": str(a.id), "title": a.title, "watcher_type": wt}
    finally:
        await eng.dispose()


def _zone(tz: str):
    """ZoneInfo do fuso do usuário; UTC se vazio/ inválido."""
    if tz:
        try:
            return ZoneInfo(tz)
        except Exception:  # noqa: BLE001
            pass
    return timezone.utc


def _parse_when(in_minutes, at: str, now: datetime, tz: str = "") -> tuple[datetime | None, str | None]:
    """Calcula o horário de disparo (UTC, tz-aware) a partir de `in_minutes`
    (relativo) OU `at` ("HH:MM" ou ISO 'YYYY-MM-DD HH:MM'). `at`/'HH:MM' são
    interpretados no FUSO do usuário (`tz`), não em UTC — senão 'me lembra às 14h'
    dispararia 3h fora no BRT. Devolve sempre em UTC."""
    if in_minutes not in (None, ""):
        try:
            mins = float(in_minutes)
        except (TypeError, ValueError):
            return None, "in_minutes must be a number"
        if mins <= 0:
            return None, "in_minutes must be greater than 0"
        return now + timedelta(minutes=mins), None
    at = (at or "").strip()
    if not at:
        return None, "provide `in_minutes` or `at`"
    zone = _zone(tz)
    local_now = now.astimezone(zone)
    m = _HHMM_RE.match(at)
    if m:
        h, mm = int(m.group(1)), int(m.group(2))
        if h > 23 or mm > 59:
            return None, "invalid time in `at` (use HH:MM)"
        cand = local_now.replace(hour=h, minute=mm, second=0, microsecond=0)
        if cand <= local_now:
            cand += timedelta(days=1)  # já passou hoje -> amanhã
        return cand.astimezone(timezone.utc), None
    try:
        dt = datetime.fromisoformat(at)
        if dt.tzinfo is None:  # naive -> hora local do usuário
            dt = dt.replace(tzinfo=zone)
        dt = dt.astimezone(timezone.utc)
        if dt <= now:
            return None, "`at` is in the past"
        return dt, None
    except ValueError:
        return None, "could not parse `at` (use HH:MM or YYYY-MM-DD HH:MM)"


async def create_reminder(
    user_id, *, message: str = "", in_minutes=None, at: str = "",
    target: str = "current", chat_id=None, title: str = "", tz: str = "",
) -> dict:
    """Cria um lembrete pontual (one-shot): dispara uma vez no horário e entrega
    a mensagem literal no chat (atual ou novo) + notificação in-app. SEM chamada
    de modelo — o texto é o próprio lembrete. `tz` (IANA) interpreta `at` no fuso
    local do usuário."""
    msg = (message or "").strip()
    if not msg:
        return {"error": "reminder requires a `message`"}
    now = datetime.now(timezone.utc)
    when, err = _parse_when(in_minutes, at, now, tz)
    if err:
        return {"error": err}

    try:
        uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    except (ValueError, TypeError):
        return {"error": "invalid user"}

    mode = (target or "current").strip().lower()
    if mode in ("current", "current_chat", "same") and chat_id:
        tgt = {"mode": "existing", "chat_id": str(chat_id)}
    else:
        tgt = {"mode": "new_each"}

    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            a = Automation(
                user_id=uid,
                title=(title or msg)[:255],
                kind="reminder",
                enabled=True,
                model="",
                instructions=msg,
                target=tgt,
                schedule={"one_shot": True},
                next_run_at=when,
            )
            db.add(a)
            await db.commit()
            await db.refresh(a)
            return {
                "ok": True,
                "reminder_id": str(a.id),
                "title": a.title,
                "when": when.isoformat(),
            }
    finally:
        await eng.dispose()
