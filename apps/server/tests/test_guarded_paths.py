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
