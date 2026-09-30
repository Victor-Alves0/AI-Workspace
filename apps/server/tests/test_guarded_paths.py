"""Regressão: TODO caminho que produz uma resposta normal ao usuário passa pelos guardas
de saída (run_turn_guarded). O achado do mapeamento: `continue` e o `wake` usavam
`run_turn` cru, então os guardas configurados no modelo sumiam justo ali (o wake é o
trabalho autônomo continuando — onde o guarda-juiz de fundamentação mais importa).

Estes testes são estruturais (inspecionam a fonte) — travam a fiação contra reversão sem
precisar montar DB/rotas. Ver docs/turn-pipeline.md (tabela de entry points)."""
from __future__ import annotations

import inspect

from aiworkspace.chat import messages_routes as mr
from aiworkspace.chat import resume as rz


def test_continue_message_uses_guarded():
    src = inspect.getsource(mr.continue_message)
    assert "_resolve_guards(" in src, "continue deve resolver os guardas do modelo"
    assert "run_turn_guarded(" in src, "continue deve rodar via run_turn_guarded"
    assert "source = run_turn(" not in src, "continue não pode iniciar a geração com run_turn cru"


def test_resume_wake_uses_guarded():
    src = inspect.getsource(rz.resume_chat_turn)
    assert "_resolve_guards(" in src, "o wake deve resolver os guardas do modelo"
    assert "run_turn_guarded(" in src, "o wake deve rodar via run_turn_guarded"
    assert "source = run_turn(" not in src, "o wake não pode iniciar a geração com run_turn cru"


def test_send_and_regenerate_still_guarded():
    # não regredir os caminhos que já eram guarded
    for fn in (mr.send_message, mr.regenerate_message):
        src = inspect.getsource(fn)
        assert "run_turn_guarded(" in src, f"{fn.__name__} deve continuar guarded"


def test_automation_and_public_api_use_the_models_guards():
    """Mesmo modelo, mesma regra: automação e /v1 aplicam os guardas de saída do
    preset (antes a automação rodava `run_turn` cru e a API chamava sem guardas)."""
    from aiworkspace.api import runner as api_runner
    from aiworkspace.automation import runner as auto_runner

    for fn in (auto_runner._run_scheduled, api_runner.run_platform_turn):
        src = inspect.getsource(fn)
        assert "_resolve_guards(" in src, f"{fn.__name__} deve resolver os guardas do modelo"
        assert "run_turn_guarded(" in src and "guards=guards" in src, fn.__name__
        assert "run_turn(" not in src.replace("run_turn_guarded(", ""), fn.__name__


def test_public_api_stream_never_leaks_a_rejected_attempt():
    """No streaming da API, o texto de uma tentativa rejeitada pelo guarda não pode
    chegar ao cliente (ele não tem como apagar tokens já recebidos)."""
    import asyncio
    from types import SimpleNamespace

    from aiworkspace.api import runner as api_runner

    async def fake_guarded(**kw):
        assert kw["guards"]
        yield {"type": "token", "text": "RECUSO"}
        yield {"type": "guard", "action": "reinforce"}
        yield {"type": "guard_reset"}
        yield {"type": "token", "text": "Resposta boa"}
        yield {"type": "done", "content": "Resposta boa"}

    async def fake_async(*a, **k):
        return [{"id": "g"}]

    async def none_async(*a, **k):
        return None

    async def empty(*a, **k):
        return []

    orig = {n: getattr(api_runner, n) for n in (
        "run_turn_guarded", "_resolve_guards", "get_sift_for_user", "_load_skills",
        "_resolve_knowledge", "_resolve_brain", "memory_opts", "build_params", "_code_mode",
        "_realtime_datetime")}
    api_runner.run_turn_guarded = fake_guarded
    api_runner._resolve_guards = fake_async
    api_runner.get_sift_for_user = none_async
    api_runner._load_skills = empty
    api_runner._resolve_knowledge = lambda *a, **k: None
    api_runner._resolve_brain = lambda *a, **k: None
    api_runner.memory_opts = lambda *a, **k: (None, None, None)
    api_runner.build_params = lambda *a, **k: {}
    api_runner._code_mode = lambda *a, **k: False
    api_runner._realtime_datetime = lambda *a, **k: None
    try:
        ctx = SimpleNamespace(db=None, user=SimpleNamespace(id="u", profile={}), key=None)
        rm = SimpleNamespace(config=None, api_key="k", base_url=None, base_model="m")
        parsed = SimpleNamespace(system=None, history=[], user_text="oi", attachments=None)

        async def go():
            return [ev async for ev in api_runner.run_platform_turn(ctx, rm, parsed, {})]

        evs = asyncio.run(go())
    finally:
        for n, f in orig.items():
            setattr(api_runner, n, f)
    textos = [e["text"] for e in evs if e["type"] == "token"]
    assert textos == ["Resposta boa"]
    assert evs[-1]["type"] == "done"
