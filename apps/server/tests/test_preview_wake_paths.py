"""Bateria dos caminhos do poller de readiness do preview (_ready_poller).

Cobre os desfechos além do 'subiu': servidor CAIU, TETO de tempo estourado ainda subindo,
chat OCUPADO (não acorda no meio de outra geração), preview REAPEADO (limpa o watcher sem
acordar) e chats INDEPENDENTES (cada um acorda o seu). São GUARDS do comportamento correto.

Hermético: Preview falso (sem subprocesso, status controlado); resume_chat_turn e
generation.get_active com stubs. O poller tem ciclo de ~3s, então cada teste espera alguns s.
"""
from __future__ import annotations

import asyncio
import time

from aiworkspace.codespace import preview_service as pv_mod


def _mk(status: str = "starting", project: str = "p1", port: int = 4001):
    pv = pv_mod.Preview(user_id="u1", project_id=project, command="npm run dev",
                        port=port, expose="localhost", host="0.0.0.0")
    pv.status = lambda: status
    return pv


class _Gen:
    def __init__(self, done): self.done = done


def _install(resume_fn, active_fn):
    import aiworkspace.chat.resume as resume_mod
    from aiworkspace.chat import generation as gen
    orig = (resume_mod.resume_chat_turn, gen.get_active)
    resume_mod.resume_chat_turn = resume_fn
    gen.get_active = active_fn
    return orig


def _restore(orig):
    import aiworkspace.chat.resume as resume_mod
    from aiworkspace.chat import generation as gen
    resume_mod.resume_chat_turn, gen.get_active = orig


async def _poll_until(cond, timeout=8.0):
    task = asyncio.create_task(pv_mod._ready_poller())
    try:
        steps = int(timeout / 0.2)
        for _ in range(steps):
            if cond():
                return
            await asyncio.sleep(0.2)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def test_guard_wake_quando_cai():
    woke = []
    async def rez(cid, note, *, notify_title, notify_body=""):
        woke.append((cid, notify_title, note))
    orig = _install(rez, lambda cid: None)
    pv = _mk("starting"); pv_mod._previews[pv.id] = pv
    try:
        async def flow():
            pv_mod.watch_ready("u1", "p1", pv.id, "cA")
            pv.status = lambda: "crashed"
            await _poll_until(lambda: bool(woke))
        asyncio.run(flow())
        assert woke and woke[0][1] == "Servidor caiu", woke
        assert "logs" in woke[0][2]
        assert pv.id not in pv_mod._ready_watch
    finally:
        _restore(orig); pv_mod._previews.pop(pv.id, None); pv_mod._ready_watch.pop(pv.id, None)


def test_guard_wake_quando_estoura_o_teto_ainda_subindo():
    woke = []
    async def rez(cid, note, *, notify_title, notify_body=""):
        woke.append((cid, notify_title, note))
    orig = _install(rez, lambda cid: None)
    pv = _mk("starting"); pv_mod._previews[pv.id] = pv
    try:
        async def flow():
            pv_mod.watch_ready("u1", "p1", pv.id, "cA")
            # força expiração do teto (continua 'starting')
            pv_mod._ready_watch[pv.id]["deadline"] = time.monotonic() - 1
            await _poll_until(lambda: bool(woke))
        asyncio.run(flow())
        assert woke and woke[0][1] == "Servidor demorando", woke
    finally:
        _restore(orig); pv_mod._previews.pop(pv.id, None); pv_mod._ready_watch.pop(pv.id, None)


def test_guard_nao_acorda_chat_ocupado_ate_ficar_ocioso():
    woke = []
    busy = {"on": True}
    async def rez(cid, note, *, notify_title, notify_body=""):
        woke.append(cid)
    def active(cid):
        return _Gen(done=False) if busy["on"] else None
    orig = _install(rez, active)
    pv = _mk("starting"); pv_mod._previews[pv.id] = pv
    try:
        async def flow():
            pv_mod.watch_ready("u1", "p1", pv.id, "cB")  # registra enquanto 'starting'
            pv.status = lambda: "up"                       # subiu, mas o chat está ocupado
            # ocupado ~4s: NÃO pode acordar
            await _poll_until(lambda: bool(woke), timeout=4.0)
            assert not woke, "acordou com o chat ainda gerando (corrida de 2 gerações)"
            busy["on"] = False  # ficou ocioso → agora pode
            await _poll_until(lambda: bool(woke), timeout=6.0)
        asyncio.run(flow())
        assert woke == ["cB"], woke
    finally:
        _restore(orig); pv_mod._previews.pop(pv.id, None); pv_mod._ready_watch.pop(pv.id, None)


def test_guard_preview_reapeado_limpa_watcher_sem_acordar():
    woke = []
    async def rez(*a, **k):
        woke.append(1)
    orig = _install(rez, lambda cid: None)
    pv = _mk("starting"); pv_mod._previews[pv.id] = pv
    try:
        async def flow():
            pv_mod.watch_ready("u1", "p1", pv.id, "cC")
            pv_mod._previews.pop(pv.id, None)  # o reaper derrubou o preview
            await _poll_until(lambda: pv.id not in pv_mod._ready_watch)
        asyncio.run(flow())
        assert pv.id not in pv_mod._ready_watch, "watcher órfão não foi limpo"
        assert not woke, "não deveria acordar por um preview que sumiu"
    finally:
        _restore(orig); pv_mod._previews.pop(pv.id, None); pv_mod._ready_watch.pop(pv.id, None)


def test_guard_chats_independentes_cada_um_acorda_o_seu():
    woke = []
    async def rez(cid, note, *, notify_title, notify_body=""):
        woke.append(cid)
    orig = _install(rez, lambda cid: None)
    a = _mk("starting", project="pA", port=4001); b = _mk("starting", project="pB", port=4002)
    pv_mod._previews[a.id] = a; pv_mod._previews[b.id] = b
    try:
        async def flow():
            pv_mod.watch_ready("u1", "pA", a.id, "c1")   # registra enquanto 'starting'
            pv_mod.watch_ready("u1", "pB", b.id, "c2")
            a.status = lambda: "up"; b.status = lambda: "up"
            await _poll_until(lambda: len(woke) >= 2)
        asyncio.run(flow())
        assert set(woke) == {"c1", "c2"}, woke
    finally:
        _restore(orig)
        pv_mod._previews.pop(a.id, None); pv_mod._previews.pop(b.id, None)
        pv_mod._ready_watch.pop(a.id, None); pv_mod._ready_watch.pop(b.id, None)
