"""Auto-observabilidade: a saúde das CAPACIDADES do harness (ver models/health_event).

`record(...)` é SÍNCRONO (psycopg2, fire-and-forget, NUNCA levanta) de propósito —
os pontos de degradação vivem tanto em código sync (mem0_service via psycopg2) quanto
async (orchestrator), e um evento de saúde jamais pode derrubar o caminho que estava
tentando observar. Eventos raros (degradações), então conectar por evento é barato.

Severidades: info (nota) < warn (fallback leve) < degraded (capacidade caída/rebaixada)
< error (falha). Em `degraded|error` dispara um ALARME (Notification pros admins),
com cooldown em memória p/ não floodar.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
import uuid
from typing import Any
from urllib.parse import urlparse

import psycopg2
from psycopg2.extras import Json
from sqlalchemy import func, select

from .config import get_settings

logger = logging.getLogger(__name__)

_ALARM_SEVERITIES = {"degraded", "error"}
# cooldown por (capability,event): não realarma antes disso (em memória; um restart
# rearma de propósito — restart é justamente quando você quer reconferir a saúde).
_ALARM_COOLDOWN_S = 900
_last_alarm: dict[str, float] = {}


def _conn():
    url = urlparse(get_settings().sync_database_url)
    return psycopg2.connect(
        dbname=url.path.lstrip("/"), user=url.username, password=url.password,
        host=url.hostname, port=url.port or 5432, connect_timeout=10,
    )


def record(capability: str, event: str, *, severity: str = "info",
           detail: dict[str, Any] | None = None, user_id: str | None = None,
           chat_id: str | None = None) -> None:
    """Registra um evento de saúde. Fire-and-forget: NUNCA levanta — se o próprio
    registro falhar, só loga e segue (não pode derrubar o caminho observado)."""
    try:
        uid = str(user_id) if user_id else None
        cid = str(chat_id) if chat_id else None
        conn = _conn()
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO health_events (id, capability, event, severity, user_id, "
                "chat_id, detail, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s, now(), now())",
                (str(uuid.uuid4()), capability[:32], event[:48], severity[:12],
                 uid, cid, Json(detail or {})),
            )
        conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("health.record falhou (%s/%s): %s", capability, event, exc)
        return
    if severity in _ALARM_SEVERITIES:
        _maybe_alarm(capability, event, severity, detail or {})


# tasks de escrita offloadada EM VOO — guarda a referência p/ o GC não as coletar antes
# de concluir (senão "Task/Future was destroyed but it is pending"); o callback as remove.
_bg_pending: set = set()


def record_bg(capability: str, event: str, *, severity: str = "info",
              detail: dict[str, Any] | None = None, user_id: str | None = None,
              chat_id: str | None = None) -> None:
    """Versão NÃO-BLOQUEANTE de `record()` p/ call sites em contexto async no MAIN loop
    (orchestrator `_health`, compactação, reaper). `record()` é psycopg2 síncrono
    (connect+insert, connect_timeout=10s) — chamá-lo direto no event loop o TRAVA até 10s
    se o banco engasgar, e eventos de saúde disparam justamente quando algo já degrada.
    Aqui a escrita vai p/ um thread do executor: o loop nunca bloqueia. Fire-and-forget.
    Sem loop rodando (contexto sync — threads do mem0/exec, self-check do boot), cai no
    `record()` inline, que é o correto ali (não há loop a proteger)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        record(capability, event, severity=severity, detail=detail,
               user_id=user_id, chat_id=chat_id)
        return
    fut = loop.run_in_executor(None, functools.partial(
        record, capability, event, severity=severity, detail=detail,
        user_id=user_id, chat_id=chat_id))
    _bg_pending.add(fut)
    fut.add_done_callback(_bg_pending.discard)


def _maybe_alarm(capability: str, event: str, severity: str, detail: dict) -> None:
    """Cria uma Notification pros admins quando uma capacidade degrada — com cooldown
    por (capability,event). Silencioso e à prova de falha."""
    key = f"{capability}:{event}"
    now = time.monotonic()
    # "nunca alarmou" é None, e NÃO 0.0. `time.monotonic()` conta desde o boot da
    # máquina, então logo depois de subir ele vale poucas centenas de segundos —
    # com o default 0.0, `now - last < 900` dava VERDADEIRO e o alarme era engolido
    # durante os primeiros 15 minutos de vida do processo. Ou seja: a observabilidade
    # ficava muda exatamente na janela em que mais se degrada (boot, self-check,
    # configuração errada), e sem deixar rastro — nem log de erro.
    last = _last_alarm.get(key)
    if last is not None and now - last < _ALARM_COOLDOWN_S:
        return
    _last_alarm[key] = now
    try:
        reason = str(detail.get("reason") or detail.get("error") or "").strip()
        title = f"Saúde: {capability} {severity}"
        body = f"[{severity}] {capability} → {event}" + (f": {reason[:300]}" if reason else "")
        conn = _conn()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE role = 'admin'")
            admin_ids = [r[0] for r in cur.fetchall()]
            for aid in admin_ids:
                cur.execute(
                    "INSERT INTO notifications (id, user_id, title, body, read, "
                    "created_at, updated_at) VALUES (%s,%s,%s,%s,false, now(), now())",
                    (str(uuid.uuid4()), aid, title[:255], body),
                )
        conn.close()
        logger.warning("ALARME de saúde: %s", body)
    except Exception as exc:  # noqa: BLE001
        logger.warning("health._maybe_alarm falhou (%s): %s", key, exc)


# --------------------------------------------------------------------------- #
# Leitura (admin) — snapshot recente por capacidade
# --------------------------------------------------------------------------- #
async def snapshot(db, hours: int = 24) -> dict:
    """Resumo das últimas `hours` h: contagem por (capacidade, severidade) + o evento
    mais recente de cada capacidade. `db` é a AsyncSession da requisição do admin."""
    from datetime import datetime, timedelta, timezone

    from .models import HealthEvent

    since = datetime.now(timezone.utc) - timedelta(hours=max(1, min(hours, 720)))
    rows = list(await db.execute(
        select(HealthEvent.capability, HealthEvent.severity, func.count())
        .where(HealthEvent.created_at >= since)
        .group_by(HealthEvent.capability, HealthEvent.severity)
    ))
    by_cap: dict[str, dict] = {}
    for cap, sev, n in rows:
        by_cap.setdefault(cap, {"capability": cap, "counts": {}, "worst": "info"})
        by_cap[cap]["counts"][sev] = int(n)
    order = {"info": 0, "warn": 1, "degraded": 2, "error": 3}
    for cap, info in by_cap.items():
        info["worst"] = max(info["counts"], key=lambda s: order.get(s, 0)) if info["counts"] else "info"
        latest = (await db.scalars(
            select(HealthEvent).where(HealthEvent.capability == cap)
            .order_by(HealthEvent.created_at.desc()).limit(1)
        )).first()
        if latest is not None:
            info["latest"] = {
                "event": latest.event, "severity": latest.severity,
                "detail": latest.detail or {},
                "at": latest.created_at.isoformat() if latest.created_at else None,
            }
    # o card de saúde é sobre PROBLEMAS: só capacidades com degradação (warn+). O
    # telemetria de primitivos (severity=info) fica de fora daqui — vive em
    # primitive_metrics(), o outro eixo (medição de uso, não de saúde).
    caps = [c for c in by_cap.values() if c["worst"] in ("warn",) + tuple(_ALARM_SEVERITIES)]
    caps.sort(key=lambda c: order.get(c["worst"], 0), reverse=True)
    healthy = all(c["worst"] not in _ALARM_SEVERITIES for c in caps)
    return {"ok": healthy, "hours": hours, "capabilities": caps}


# --------------------------------------------------------------------------- #
# Medição de PRIMITIVOS (frente 2) — com que frequência cada primitivo age e com
# que DESFECHO. Mesmo stream de eventos; lente diferente (uso, não saúde).
# --------------------------------------------------------------------------- #
# nomes de capacidade que são PRIMITIVOS do harness (não infra) — o que medimos aqui.
_PRIMITIVES = {
    "synthesis", "tool_watchdog", "steering", "queue", "anti_spin",
    "output_guard", "guard_judge", "ledger", "compaction", "codegraph",
}


async def primitive_metrics(db, days: int = 7) -> dict:
    """Quantas vezes cada primitivo AGIU e com que desfecho, nos últimos `days` dias.
    Conta TODAS as severidades (o disparo normal é info; o fallback é warn/degraded)."""
    from datetime import datetime, timedelta, timezone

    from .models import HealthEvent

    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 90)))
    rows = list(await db.execute(
        select(HealthEvent.capability, HealthEvent.event, HealthEvent.severity, func.count())
        .where(HealthEvent.created_at >= since, HealthEvent.capability.in_(_PRIMITIVES))
        .group_by(HealthEvent.capability, HealthEvent.event, HealthEvent.severity)
    ))
    prims: dict[str, dict] = {}
    for cap, event, sev, n in rows:
        p = prims.setdefault(cap, {"primitive": cap, "total": 0, "events": {}})
        p["total"] += int(n)
        p["events"][event] = p["events"].get(event, 0) + int(n)
        # marca se houve fallback/degradação (sinal de qualidade)
        if sev in _ALARM_SEVERITIES:
            p["degraded"] = p.get("degraded", 0) + int(n)
    out = sorted(prims.values(), key=lambda p: p["total"], reverse=True)
    return {"days": days, "primitives": out}


# --------------------------------------------------------------------------- #
# Self-check de boot — pinga as capacidades e grava o estado (down|recovered)
# --------------------------------------------------------------------------- #
def self_check() -> dict:
    """Verifica no boot se as capacidades centrais estão de pé e grava um evento
    (down/degraded se caíram). SÍNCRONO (psycopg2 + mem0.warm bloqueiam) — chamado do
    lifespan via run_in_threadpool p/ não travar o loop. Nunca levanta."""
    results: dict[str, bool] = {}

    # banco
    try:
        conn = _conn()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.close()
        results["database"] = True
    except Exception as exc:  # noqa: BLE001
        results["database"] = False
        record("database", "down", severity="error", detail={"error": str(exc)})

    # mem0: CONSTRUIR o cliente é o teste — é exatamente aqui que degradou silencioso
    # p/ no-op (config inválida rejeitada pelo MemoryConfig). `warm` valida o schema +
    # pgvector + embedder; a chave sentinela só exercita a construção (o LLM é lazy).
    try:
        from .memory import mem0_service
        ok = bool(mem0_service.warm("healthcheck"))
        results["memory"] = ok
        if not ok:
            record("memory", "no_op", severity="degraded",
                   detail={"reason": "mem0 não construiu no boot (config/pgvector/embedder)"})
    except Exception as exc:  # noqa: BLE001
        results["memory"] = False
        record("memory", "no_op", severity="degraded", detail={"error": str(exc)})

    # segredos no AMBIENTE + execução ligada: um comando executado pela IA (injeção de
    # prompt basta) recupera APP_SECRET/DATABASE_URL de /proc/1/environ e, com eles, os
    # tokens de integração de todos os usuários. Alarma p/ o admin em vez de depender de
    # alguém lembrar. Some ao migrar p/ APP_SECRET_FILE. Ver docs/trust-model.md.
    try:
        s = get_settings()
        leaking = s.secrets_in_env
        results["secrets_from_file"] = not leaking
        if leaking and s.allow_code_mode:
            record("secrets", "in_env", severity="degraded", detail={
                "vars": ",".join(leaking),
                "reason": "segredo via ambiente fica legível em /proc/1/environ para "
                          "comandos executados pela IA — use APP_SECRET_FILE/DATABASE_URL_FILE",
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning("self-check de segredos falhou: %s", exc)

    logger.info("self-check de saúde: %s", results)
    return results
