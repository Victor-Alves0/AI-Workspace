"""Readiness -> WAKE do preview: um servidor NÃO 'termina', então o wake de conclusão do
exec_jobs não serve. A IA registra `watch_ready` e encerra o turno; o poller acorda o chat
quando o server fica 'up' (ou cai). Fecha a lacuna do bug do Metabase (a promessa de
'continuo monitorando sozinho' que nunca se cumpria).

Hermético: constrói um Preview FALSO (sem subprocesso) e força o status; o wake é testado
com stubs de resume_chat_turn/generation.get_active.
"""
from __future__ import annotations

import asyncio

from aiworkspace.codespace import preview_service as pv_mod


def _mk_preview(status_val: str = "starting") -> "pv_mod.Preview":
    pv = pv_mod.Preview(user_id="u1", project_id="p1", command="npm run dev",
                        port=4001, expose="localhost", host="0.0.0.0")
    pv.status = lambda: status_val   # sombra da instância → controla o status sem abrir porta
    return pv


def test_watch_ready_exige_chat_e_registra():
    pv = _mk_preview("starting")
    pv_mod._previews[pv.id] = pv
    try:
        # sem chat_id não há quem acordar → erro (use wait, que bloqueia)
        assert "error" in pv_mod.watch_ready("u1", "p1", pv.id, chat_id="")
        # com chat e 'starting' → registra o watcher
        r = pv_mod.watch_ready("u1", "p1", pv.id, chat_id="c1")
        assert r.get("ok") and pv.id in pv_mod._ready_watch
        # já 'up' → não registra, manda seguir direto
        pv.status = lambda: "up"
        r2 = pv_mod.watch_ready("u1", "p1", pv.id, chat_id="c1")
        assert "note" in r2 and "já está no ar" in r2["note"]
    finally:
        pv_mod._previews.pop(pv.id, None)
        pv_mod._ready_watch.pop(pv.id, None)


def test_poller_acorda_o_chat_quando_sobe():
    import aiworkspace.chat.resume as resume_mod
    from aiworkspace.chat import generation as gen

    woke: list = []

    async def fake_resume(chat_id, note, *, notify_title, notify_body=""):
        woke.append((str(chat_id), notify_title, note))

    orig_resume, orig_active = resume_mod.resume_chat_turn, gen.get_active
    resume_mod.resume_chat_turn = fake_resume
    gen.get_active = lambda cid: None            # chat ocioso → pode acordar

    pv = _mk_preview("starting")
    pv_mod._previews[pv.id] = pv

    async def _flow():
        # registra o wake enquanto 'starting'
        assert pv_mod.watch_ready("u1", "p1", pv.id, chat_id="c1").get("ok")
        task = asyncio.create_task(pv_mod._ready_poller())
        try:
            pv.status = lambda: "up"             # servidor subiu
            for _ in range(40):                  # espera o poller (ciclo de 3s) disparar
                if woke:
                    break
                await asyncio.sleep(0.2)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        assert woke and woke[0][0] == "c1", woke
        assert woke[0][1] == "Servidor no ar"
        assert "4001" in woke[0][2]              # a nota cita a porta
        assert pv.id not in pv_mod._ready_watch  # watcher consumido

    try:
        asyncio.run(_flow())
    finally:
        resume_mod.resume_chat_turn, gen.get_active = orig_resume, orig_active
        pv_mod._previews.pop(pv.id, None)
        pv_mod._ready_watch.pop(pv.id, None)
