"""Mesa-redonda como equipe de agentes: cada fala é um turno real (com ferramentas),
o transcript diz quem disse o quê, e o laço para quando o trabalho acaba.

Regressão do "roleplay": antes cada participante recebia uma persona e um turno SEM
ferramentas (use_tools=False) — inventava dados e só conversava com os outros."""
from __future__ import annotations

import asyncio
import inspect

import pytest

from aiworkspace.chat import orchestrator as orch
from aiworkspace.chat import roundtable as rt
from aiworkspace.chat import roundtable_routes
from aiworkspace.chat.orchestrator import TurnSession, run_turn
from tests.test_run_turn_integration import FakeSift, _chunk, _fake_stream_from

A, B = "pa", "pb"
NAMES = {A: "Analista", B: "Revisor"}
SPEAKERS = {A: {"id": A, "name": "Analista"}, B: {"id": B, "name": "Revisor"}}


def _usage():
    return {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}


class Store:
    """persist falso: guarda o que seria gravado como mensagem."""

    def __init__(self):
        self.saved: list[dict] = []

    async def __call__(self, pid, sp, content, reasoning, col):
        self.saved.append({"pid": pid, "content": content, "tools": col.get("tools")})
        return {"message_id": f"m{len(self.saved)}", "content": content}


def _real_turn(sift, seen: list):
    """turno de verdade (run_turn com ferramentas), registrando o que cada agente viu."""
    async def turn(pid, history, user_text):
        seen.append({"pid": pid, "history": history, "user_text": user_text})
        async for ev in run_turn(
            api_key="k", model="m", history=history, user_text=user_text,
            chat_system_prompt="sys", params={}, session=TurnSession(user_id="u"),
            sift=sift, use_tools=True,
        ):
            yield ev
    return turn


async def _collect(agen):
    return [ev async for ev in agen]


# ------------------------------ prompts --------------------------------------

def test_system_do_participante_e_equipe_sem_encenacao_e_proibe_inventar():
    s = rt.build_system("Prompt do preset", "revisar segurança", "Revisor", ["Analista"])
    assert s.startswith("Prompt do preset")
    assert "Sua função nesta equipe: revisar segurança" in s
    assert "NUNCA invente" in s and "ferramentas" in s
    assert "não é um debate nem uma encenação" in s
    assert rt.DONE_MARK in s and rt.ASK_MARK in s
    assert "persona" not in s.lower()


def test_visao_do_participante_rotula_quem_disse_o_que_e_funde_mensagens_seguidas():
    convo = [
        {"role": "user", "content": "analise o repo", "speaker": None},
        {"role": "assistant", "content": "li main.py", "speaker": A, "tools": ["code.files.browse"]},
        {"role": "assistant", "content": "concordo", "speaker": B},
    ]
    hist, text = rt.participant_view(convo, NAMES, B, "Revisor")
    # a fala do próprio B vira assistant; o bloco anterior (usuário + colega) é um só user
    assert [m["role"] for m in hist] == ["user", "assistant"]
    assert hist[0]["content"].startswith("[Usuário]\nanalise o repo")
    assert "[Analista]\nli main.py" in hist[0]["content"]
    assert "ferramentas usadas: code.files.browse" in hist[0]["content"]
    assert hist[1]["content"] == "concordo"
    assert text.startswith("Sua vez, Revisor")

    hist_a, text_a = rt.participant_view(convo[:2], NAMES, B, "Revisor")
    # a última mensagem de outros vai junto do pedido da vez (sem user duplicado)
    assert hist_a == []
    assert "[Usuário]" in text_a and "[Analista]" in text_a and "Sua vez, Revisor" in text_a


def test_marcadores_de_parada_sao_removidos_e_viram_desfecho():
    assert rt.split_marks("pronto\n[[FIM]]") == ("pronto", "done")
    assert rt.split_marks("qual branch?\n[[ AGUARDANDO_USUARIO ]]") == ("qual branch?", "ask")
    assert rt.split_marks("sem marca") == ("sem marca", None)


def test_resposta_do_moderador_aceita_numero_nome_ou_fim():
    parts = [{"id": A, "name": "Analista"}, {"id": B, "name": "Revisor"}]
    assert rt.parse_moderator("2", parts) == B
    assert rt.parse_moderator("  1.", parts) == A
    assert rt.parse_moderator("Revisor", parts) == B
    assert rt.parse_moderator("FIM", parts) == "STOP"
    assert rt.parse_moderator("não sei", parts) is None
    assert rt.parse_moderator("9", parts) is None


def test_rota_da_mesa_nao_desliga_ferramentas_do_participante():
    src = inspect.getsource(roundtable_routes.roundtable_run)
    assert "use_tools=False" not in src
    assert "get_sift_for_user" in src and "run_turn_guarded" in src
    assert "_load_skills" in src and "_memory_opts" in src and "_resolve_knowledge" in src


# ------------------------------ laço -----------------------------------------

async def test_participante_usa_ferramenta_de_verdade_e_colega_constroi_em_cima(monkeypatch):
    scripts = [
        # Analista: chama a ferramenta e responde com o dado real
        [_chunk(tool=("web.search.query", '{"query":"repo"}'), finish="tool_calls")],
        [_chunk(content="Encontrei 3 arquivos.", finish="stop", usage=_usage())],
        # Revisor: consolida e encerra
        [_chunk(content="Resumo final: 3 arquivos.\n[[FIM]]", finish="stop", usage=_usage())],
    ]
    fake, _ = _fake_stream_from(scripts)
    monkeypatch.setattr(orch.openrouter, "stream_chat", fake)
    sift = FakeSift({"web.search.query": {"files": ["a.py", "b.py", "c.py"]}})
    seen: list = []
    store = Store()
    convo = [{"role": "user", "content": "liste os arquivos do repo", "speaker": None}]
    events = await _collect(rt.run_loop(
        order=[A, B], speakers=SPEAKERS, names=NAMES, convo=convo,
        turn=_real_turn(sift, seen), persist=store, cap=10,
    ))
    calls = [e for e in events if e["type"] == "tool_call"]
    assert calls and calls[0]["speaker"] == A  # a UI mostra o passo de ferramenta do agente
    assert not any(e["type"] == "done" for e in events)
    # a fala do Analista foi gravada com os tool_events
    assert store.saved[0]["pid"] == A
    assert ("call", "web.search.query") in [(t["kind"], t["name"]) for t in store.saved[0]["tools"]]
    # o Revisor viu o que o Analista fez (e com quais ferramentas)
    assert "[Analista]\nEncontrei 3 arquivos." in seen[1]["user_text"]
    assert "ferramentas usadas: web.search.query" in seen[1]["user_text"]
    # o marcador encerra a mesa e sai do texto gravado
    assert store.saved[1]["content"] == "Resumo final: 3 arquivos."
    ends = [e for e in events if e["type"] == "speaker_end"]
    assert ends[1]["outcome"] == "done" and ends[1]["content"] == "Resumo final: 3 arquivos."
    assert events[-1] == {"type": "roundtable_done", "reason": "done"}


def _scripted(replies: dict[str, list[str]]):
    idx = {k: 0 for k in replies}

    async def turn(pid, history, user_text):
        i = idx[pid]
        idx[pid] += 1
        text = replies[pid][min(i, len(replies[pid]) - 1)]
        yield {"type": "token", "text": text}
        yield {"type": "done", "content": text, "usage": _usage()}
    return turn


async def test_mesa_para_no_teto_de_turnos_sem_conversa_infinita():
    store = Store()
    convo = [{"role": "user", "content": "x", "speaker": None}]
    events = await _collect(rt.run_loop(
        order=[A, B], speakers=SPEAKERS, names=NAMES, convo=convo,
        turn=_scripted({A: ["a"], B: ["b"]}), persist=store, cap=4,
    ))
    assert len(store.saved) == 4
    assert events[-1] == {"type": "roundtable_done", "reason": "limit"}


async def test_pergunta_ao_usuario_encerra_e_um_passo_roda_so_uma_fala():
    store = Store()
    convo = [{"role": "user", "content": "x", "speaker": None}]
    events = await _collect(rt.run_loop(
        order=[A, B], speakers=SPEAKERS, names=NAMES, convo=convo,
        turn=_scripted({A: ["qual pasta?\n[[AGUARDANDO_USUARIO]]"], B: ["b"]}), persist=store, cap=10,
    ))
    assert events[-1]["reason"] == "ask" and len(store.saved) == 1

    store2 = Store()
    events2 = await _collect(rt.run_loop(
        order=[A, B], speakers=SPEAKERS, names=NAMES, convo=list(convo),
        turn=_scripted({A: ["a"], B: ["b"]}), persist=store2, cap=10, one_step=True,
    ))
    assert events2[-1]["reason"] == "step" and len(store2.saved) == 1


async def test_mensagem_do_usuario_no_meio_vira_instrucao_para_a_proxima_fala():
    inbox = [[], ["agora foque no módulo X"]]
    seen: list = []
    replies = _scripted({A: ["feito\n[[FIM]]"], B: ["X analisado\n[[FIM]]"]})

    async def turn(pid, history, user_text):
        seen.append(user_text)
        async for ev in replies(pid, history, user_text):
            yield ev

    events = await _collect(rt.run_loop(
        order=[A, B], speakers=SPEAKERS, names=NAMES,
        convo=[{"role": "user", "content": "analise", "speaker": None}],
        turn=turn, persist=Store(), cap=10, drain_user=lambda: inbox.pop(0) if inbox else [],
    ))
    # o FIM do 1º agente não encerrou: havia instrução nova do usuário
    assert len(seen) == 2 and "[Usuário]\nagora foque no módulo X" in seen[1]
    assert events[-1]["reason"] == "done"


async def test_moderador_escolhe_quem_age_e_pode_encerrar():
    answers = iter([B, "STOP"])

    async def pick(_convo):
        return next(answers)

    store = Store()
    events = await _collect(rt.run_loop(
        order=[A, B], speakers=SPEAKERS, names=NAMES,
        convo=[{"role": "user", "content": "x", "speaker": None}],
        turn=_scripted({A: ["a"], B: ["b"]}), persist=store, cap=10,
        policy="moderator", pick=pick,
    ))
    assert [s["pid"] for s in store.saved] == [B]
    assert events[-1] == {"type": "roundtable_done", "reason": "moderator"}


async def test_pausar_no_meio_da_fala_salva_o_parcial():
    store = Store()
    started = asyncio.Event()

    async def turn(pid, history, user_text):
        yield {"type": "token", "text": "lendo arquivos"}
        started.set()
        await asyncio.sleep(30)
        yield {"type": "done", "content": "nunca"}  # pragma: no cover

    async def drive():
        async for _ in rt.run_loop(
            order=[A], speakers=SPEAKERS, names=NAMES,
            convo=[{"role": "user", "content": "x", "speaker": None}],
            turn=turn, persist=store, cap=5,
        ):
            pass

    task = asyncio.create_task(drive())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.saved and store.saved[0]["content"] == "lendo arquivos"


async def test_agentes_sem_saida_seguidos_encerram_a_mesa():
    async def mudo(pid, history, user_text):
        yield {"type": "error", "message": "provedor fora"}

    events = await _collect(rt.run_loop(
        order=[A, B], speakers=SPEAKERS, names=NAMES,
        convo=[{"role": "user", "content": "x", "speaker": None}],
        turn=mudo, persist=Store(), cap=20,
    ))
    assert events[-1] == {"type": "roundtable_done", "reason": "stalled"}
    assert sum(1 for e in events if e["type"] == "speaker_start") == 2
