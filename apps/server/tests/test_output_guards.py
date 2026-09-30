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


# --- Livro de efeitos: a nova tentativa NÃO repete ação já feita (tools/effects.py) ---

class _FakeSift:
    """Só o funil que importa: execute_tool, que conta as execuções REAIS."""

    def __init__(self):
        self.ran: list[tuple[str, dict]] = []

    def execute_tool(self, path, params=None):
        self.ran.append((path, dict(params or {})))
        return {"ok": True, "n": len(self.ran)}


def _turn_with_tools(sift, calls_per_attempt):
    """run_turn falso que, a cada tentativa, chama as tools como o modelo faria (numa
    thread com o contexto copiado, igual ao _dispatch_tp) e depois responde."""
    import contextvars
    import functools
    attempts: list[dict] = []

    async def fake(**kw):
        attempts.append(kw)
        loop = asyncio.get_running_loop()
        for path, params in calls_per_attempt(len(attempts)):
            ctx = contextvars.copy_context()
            await loop.run_in_executor(None, functools.partial(ctx.run, sift.execute_tool, path, params))
        text = "REFUSED" if len(attempts) == 1 else "Pronto, e-mail enviado."
        yield {"type": "done", "content": text, "usage": None, "tool_events": None}

    return fake, attempts


def test_guard_retry_does_not_resend_email():
    from aiworkspace.tools import effects
    sift = effects.wrap_sift(_FakeSift())

    def calls(n):
        # a 2ª tentativa reescreve o corpo — continua sendo o MESMO e-mail
        body = "Oi João" if n == 1 else "Olá, João! Tudo bem?"
        return [("web.search.query", {"q": "contato do joão"}),
                ("google.gmail.mailbox", {"action": "send", "to": "joao@x.com", "body": body})]

    fake, attempts = _turn_with_tools(sift, calls)
    guard = {"id": "g", "enabled": True, "detect": "regex", "pattern": "REFUSED",
             "action": "reinforce", "inject_text": "Responda.", "max_retries": 1}
    orig = _patch(fake)
    try:
        events = _run([guard], model="m", api_key="k", user_text="mande um e-mail pro João")
    finally:
        orch.run_turn = orig
    enviados = [p for p, a in sift.ran if a.get("action") == "send"]
    assert len(attempts) == 2
    assert len(enviados) == 1, "o guarda refez o turno e o e-mail foi enviado de novo"
    assert sum(1 for p, _ in sift.ran if p == "web.search.query") == 2  # leitura roda de novo
    # a 2ª tentativa fica sabendo do que já aconteceu
    assert "ALREADY DONE" in (attempts[1].get("extra_system") or "")
    assert "joao@x.com" in attempts[1]["extra_system"]
    assert [e for e in events if e["type"] == "done"][-1]["content"].startswith("Pronto")


def test_guard_retry_repeatable_write_runs_again_and_distinct_effect_runs():
    from aiworkspace.tools import effects
    sift = effects.wrap_sift(_FakeSift())

    def calls(n):
        out = [("code.files.write", {"action": "write", "path": "a.py", "content": f"v{n}"})]
        if n == 2:  # um SEGUNDO e-mail, a outra pessoa, que a 1ª tentativa não mandou
            out.append(("google.gmail.mailbox", {"action": "send", "to": "maria@x.com"}))
        return out

    fake, _ = _turn_with_tools(sift, calls)
    guard = {"id": "g", "enabled": True, "detect": "regex", "pattern": "REFUSED",
             "action": "reinforce", "inject_text": "x", "max_retries": 1}
    orig = _patch(fake)
    try:
        _run([guard], model="m", api_key="k", user_text="oi")
    finally:
        orch.run_turn = orig
    escritas = [a["content"] for p, a in sift.ran if p == "code.files.write"]
    assert escritas == ["v1", "v2"]  # arquivo melhorado é gravado de novo
    assert ("google.gmail.mailbox", {"action": "send", "to": "maria@x.com"}) in sift.ran


def test_ledger_absent_outside_guarded_turn_and_in_background_tasks():
    from aiworkspace import bg
    from aiworkspace.tools import effects

    async def main():
        tok = effects.current.set(effects.Ledger())
        try:
            seen = await bg.spawn(_peek(), trace=False)
        finally:
            effects.current.reset(tok)
        return seen

    async def _peek():
        return effects.current.get()

    assert asyncio.run(main()) is None  # agente em 2º plano não herda o livro do turno
    assert effects.current.get() is None


def test_unknown_tool_only_identical_call_is_reused():
    from aiworkspace.tools import effects
    led = effects.Ledger()
    led.record("minha.tool.custom", {"x": 1}, {"ok": 1})
    led.next_attempt()
    assert led.reuse("minha.tool.custom", {"x": 2}) == (False, None)
    hit, prev = led.reuse("minha.tool.custom", {"x": 1})
    assert hit and prev["ok"] == 1 and "replayed" in prev
    # falha não conta como feito: a nova tentativa pode tentar de novo
    led2 = effects.Ledger()
    led2.record("google.gmail.mailbox", {"action": "send"}, {"error": "token expirado"})
    led2.next_attempt()
    assert led2.reuse("google.gmail.mailbox", {"action": "send"}) == (False, None)


def test_every_risky_builtin_is_classified_in_effects_table():
    """Tool nova com efeito (risk=True) precisa dizer quais ações NÃO podem ser
    repetidas por uma nova tentativa — senão cai em OTHER e um envio com texto
    reescrito passaria como ação nova."""
    import re
    from pathlib import Path

    from aiworkspace.tools import effects
    src = Path(__file__).resolve().parents[1].joinpath("aiworkspace/tools/sift_service.py").read_text("utf-8")
    faltando = []
    for m in re.finditer(r'@sift\.tool\(\s*"([a-z0-9_.]+)"', src):
        cabeca = src[m.start():m.start() + 4000]
        cabeca = cabeca[:cabeca.find("def ", 10)]
        if "risk=True" in cabeca and m.group(1) not in effects._TABLE:
            faltando.append(m.group(1))
    assert not faltando, f"classifique em tools/effects.py::_TABLE: {faltando}"
