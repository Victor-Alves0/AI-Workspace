"""Guardas de saída (run_turn_guarded) — o loop EXTERNO ao run_turn.

Trava a semântica documentada em docs/turn-pipeline.md (estágio 3): o guarda inspeciona
só o texto FINAL de um turno inteiro e reage re-rodando (reforço) ou trocando de modelo
(fallback), com teto de tentativas. É o mecanismo do qual send/regenerate/continue/wake
dependem — se ele quebrar, os guardas somem de todos esses caminhos de uma vez."""
from __future__ import annotations

import asyncio

from aiworkspace.chat import orchestrator as orch


async def _collect(agen):
    return [ev async for ev in agen]


def _make_fake(decide):
    """Fake de run_turn: yields um token + um done cujo conteúdo `decide(kw, n)` define."""
    calls: list[dict] = []

    async def fake(**kw):
        calls.append(kw)
        yield {"type": "token", "text": "…"}
        yield {"type": "done", "content": decide(kw, len(calls)), "usage": None, "tool_events": None}

    return fake, calls


def _run(guards, **turn_kwargs):
    return asyncio.run(_collect(orch.run_turn_guarded(guards=guards, **turn_kwargs)))


def _patch(fake):
    orig = orch.run_turn
    orch.run_turn = fake
    return orig


def test_no_guards_is_passthrough():
    fake, calls = _make_fake(lambda kw, n: "resposta única")
    orig = _patch(fake)
    try:
        events = _run([], model="m", api_key="k", user_text="oi")
    finally:
        orch.run_turn = orig
    done = [e for e in events if e["type"] == "done"][-1]
    assert done["content"] == "resposta única"
    assert len(calls) == 1  # sem guarda: uma passada só


def test_reinforce_injects_extra_system_and_reruns():
    # 1ª passada recusa; com o reforço no extra_system, a 2ª entrega.
    def decide(kw, n):
        return "OK, resposta final." if "TENTE MELHOR" in (kw.get("extra_system") or "") else "REFUSED a responder."
    fake, calls = _make_fake(decide)
    guard = {"id": "g1", "enabled": True, "detect": "regex", "pattern": "REFUSED",
             "action": "reinforce", "inject_text": "TENTE MELHOR", "max_retries": 2}
    orig = _patch(fake)
    try:
        events = _run([guard], model="m", api_key="k", user_text="oi")
    finally:
        orch.run_turn = orig
    done = [e for e in events if e["type"] == "done"][-1]
    assert done["content"] == "OK, resposta final."
    assert len(calls) == 2
    assert "TENTE MELHOR" in (calls[1].get("extra_system") or "")  # reforço na 2ª
    assert any(e["type"] == "guard" and e.get("action") == "reinforce" for e in events)
    assert any(e["type"] == "guard_reset" for e in events)  # front descarta a tentativa ruim


def test_fallback_switches_model():
    def decide(kw, n):
        return "Resposta do reserva." if kw.get("model") == "backup/m" else "REFUSED."
    fake, calls = _make_fake(decide)
    guard = {"id": "g2", "enabled": True, "detect": "regex", "pattern": "REFUSED",
             "action": "fallback_model", "fallback_model": "backup/m",
             "_api_key": "k2", "_base_url": None, "max_retries": 1}
    orig = _patch(fake)
    try:
        events = _run([guard], model="m", api_key="k", user_text="oi")
    finally:
        orch.run_turn = orig
    done = [e for e in events if e["type"] == "done"][-1]
    assert done["content"] == "Resposta do reserva."
    assert calls[1]["model"] == "backup/m"  # 2ª tentativa no modelo reserva
    assert any(e["type"] == "guard" and e.get("action") == "fallback_model" for e in events)


def test_hard_cap_terminates_even_if_guard_never_satisfied():
    # guarda que NUNCA aceita + orçamento alto: o hard cap corta e emite o final mesmo assim
    # (nunca loop infinito — o usuário recebe algo).
    fake, calls = _make_fake(lambda kw, n: "REFUSED sempre.")
    guard = {"id": "g3", "enabled": True, "detect": "regex", "pattern": "REFUSED",
             "action": "reinforce", "inject_text": "X", "max_retries": 9}
    orig = _patch(fake)
    try:
        events = _run([guard], model="m", api_key="k", user_text="oi")
    finally:
        orch.run_turn = orig
    done = [e for e in events if e["type"] == "done"][-1]
    assert "REFUSED" in done["content"]  # aceito ao esgotar o cap
    assert len(calls) == orch._GUARD_HARD_CAP  # teto duro respeitado
