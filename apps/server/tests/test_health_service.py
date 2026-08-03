"""Auto-observabilidade (health_service): registro de eventos, agregação do snapshot
e o alarme com cooldown. O `record()` é sync (psycopg2) e nunca levanta; o `snapshot()`
é async. Fluxo de DB pula com graça se o Postgres não estiver acessível."""
from __future__ import annotations

import asyncio
import uuid

import pytest

from aiworkspace import health_service as hs


# --------------------------------------------------------------------------- #
# Puro (sem DB)
# --------------------------------------------------------------------------- #
def test_record_nunca_levanta_mesmo_sem_db(monkeypatch):
    # se o PRÓPRIO registro falhar, record() só loga e segue — jamais derruba o
    # caminho que estava tentando observar.
    def boom():
        raise RuntimeError("db fora")
    monkeypatch.setattr(hs, "_conn", boom)
    hs.record("memory", "no_op", severity="degraded", detail={"error": "x"})  # não levanta


def test_alarme_so_em_severidade_alta():
    assert "degraded" in hs._ALARM_SEVERITIES and "error" in hs._ALARM_SEVERITIES
    assert "info" not in hs._ALARM_SEVERITIES and "warn" not in hs._ALARM_SEVERITIES


def test_record_bg_sem_loop_roda_inline(monkeypatch):
    # fora de um contexto async não há loop a proteger → record_bg cai no record() sync.
    calls: list = []
    monkeypatch.setattr(hs, "record", lambda *a, **k: calls.append((a, k)))
    hs.record_bg("memory", "no_op", severity="degraded")
    assert calls and calls[0][0] == ("memory", "no_op")


def test_record_bg_com_loop_offloada_pra_outra_thread(monkeypatch):
    # dentro do loop, record_bg NÃO deve rodar o psycopg2 no thread do loop (bloquearia):
    # ele offloada p/ o executor → o record roda numa thread DIFERENTE e o loop segue.
    import threading
    ran: list = []
    monkeypatch.setattr(hs, "record", lambda *a, **k: ran.append(threading.get_ident()))

    async def _flow():
        main = threading.get_ident()
        hs.record_bg("synthesis", "tier_c", severity="degraded")
        for _ in range(100):          # deixa o executor rodar (sem bloquear o loop)
            if ran:
                break
            await asyncio.sleep(0.01)
        assert ran, "record_bg nunca executou o write offloadado"
        assert ran[0] != main, "o write rodou no thread do loop (deveria ser offloadado)"

    asyncio.run(_flow())


# --------------------------------------------------------------------------- #
# Fluxo de DB (Postgres real — pula se indisponível)
# --------------------------------------------------------------------------- #
def _run_db_ping() -> bool:
    try:
        c = hs._conn(); c.close(); return True
    except Exception:  # noqa: BLE001
        return False


def _mk_admin() -> str:
    uid = str(uuid.uuid4())
    c = hs._conn()
    with c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id,email,hashed_password,role,is_active,status,"
            "token_version,created_at,updated_at) VALUES "
            "(%s,%s,'x','admin',true,'active',0,now(),now())",
            (uid, f"admin-{uid[:8]}@x.test"))
    c.close()
    return uid


def _drop_user(uid: str) -> None:
    c = hs._conn()
    with c, c.cursor() as cur:
        cur.execute("DELETE FROM users WHERE id=%s", (uid,))
    c.close()


def test_record_snapshot_e_alarme_com_cooldown():
    if not _run_db_ping():
        pytest.skip("Postgres indisponível (rode dentro do container)")

    admin = _mk_admin()
    hs._last_alarm.clear()  # estado de throttle limpo p/ o teste
    try:
        async def _flow():
            from sqlalchemy import func, select
            from aiworkspace.db import SessionLocal, engine
            from aiworkspace.models import HealthEvent, Notification

            # dois eventos degradados iguais, em sequência (record é sync)
            hs.record("memory", "no_op", severity="degraded", detail={"error": "cfg"})
            hs.record("memory", "no_op", severity="degraded", detail={"error": "cfg"})
            try:
                async with SessionLocal() as db:
                    # AMBOS os eventos foram gravados
                    n_ev = await db.scalar(
                        select(func.count()).select_from(HealthEvent)
                        .where(HealthEvent.capability == "memory",
                               HealthEvent.event == "no_op"))
                    assert n_ev >= 2, n_ev
                    # mas o ALARME disparou UMA vez só (cooldown) → 1 notificação p/ o admin
                    n_notif = await db.scalar(
                        select(func.count()).select_from(Notification)
                        .where(Notification.user_id == uuid.UUID(admin)))
                    assert n_notif == 1, f"esperava 1 alarme (throttle), veio {n_notif}"
                    # snapshot: memory aparece com 'worst' = degraded
                    snap = await hs.snapshot(db, hours=24)
                    mem = next((c for c in snap["capabilities"] if c["capability"] == "memory"), None)
                    assert mem is not None and mem["worst"] == "degraded", snap
                    assert snap["ok"] is False  # há capacidade degradada
            finally:
                await engine.dispose()
            return "ok"
        asyncio.run(_flow())
    finally:
        # limpa eventos/notualizações do teste + o admin (CASCADE/SET NULL nos eventos)
        c = hs._conn()
        with c, c.cursor() as cur:
            cur.execute("DELETE FROM notifications WHERE user_id=%s", (admin,))
            cur.execute("DELETE FROM health_events WHERE capability='memory' AND event='no_op'")
        c.close()
        _drop_user(admin)


def test_primitive_metrics_agrega_uso_e_desfecho():
    if not _run_db_ping():
        pytest.skip("Postgres indisponível (rode dentro do container)")

    marker = f"tst-{uuid.uuid4().hex[:8]}"  # evento único p/ isolar do resto
    hs._last_alarm.clear()
    try:
        async def _flow():
            from aiworkspace.db import SessionLocal, engine
            # steering (info) 2x + síntese caindo p/ camada C (degraded) 1x
            hs.record("steering", f"injected_{marker}", severity="info")
            hs.record("steering", f"injected_{marker}", severity="info")
            hs.record("synthesis", f"tier_c_{marker}", severity="degraded")
            try:
                async with SessionLocal() as db:
                    m = await hs.primitive_metrics(db, days=7)
                    by = {p["primitive"]: p for p in m["primitives"]}
                    # steering contou 2, sem fallback
                    assert by["steering"]["events"].get(f"injected_{marker}") == 2
                    # synthesis marcou o desfecho degradado (sinal de qualidade)
                    assert by["synthesis"].get("degraded", 0) >= 1
            finally:
                await engine.dispose()
        asyncio.run(_flow())
    finally:
        c = hs._conn()
        with c, c.cursor() as cur:
            cur.execute("DELETE FROM health_events WHERE event LIKE %s", (f"%{marker}",))
        c.close()
