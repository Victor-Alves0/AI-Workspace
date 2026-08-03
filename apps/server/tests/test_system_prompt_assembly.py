"""Montagem do system prompt + fronteira de cache (_system_message).

Trava o invariante documentado em docs/turn-pipeline.md: o prefixo estável (chat/SIFT/
skills/brain) é o ÚNICO bloco cacheado; dados por-turno (hora, ledger, memória, reforço
de guarda) ficam FORA do cache; e o `extra_system` prioritário vem por ÚLTIMO. Se algo
per-turno vazar pro prefixo cacheado, o cache do provedor invalida a cada turno em
silêncio (custo dispara sem sintoma óbvio)."""
from __future__ import annotations

import json

from aiworkspace.chat.orchestrator import _system_message


def test_cache_boundary_excludes_per_turn_data():
    # provedor de cache explícito (anthropic/google) → content vira lista com cache_control
    msg = _system_message("STATICPREFIX", "LEDGERMEM", "anthropic/claude", "TIMENOTE", "PRIORITYX")
    content = msg["content"]
    assert isinstance(content, list)
    cached = content[0]
    assert cached.get("cache_control"), "o prefixo estável deve ser marcado p/ cache"
    assert cached["text"] == "STATICPREFIX"
    # NADA per-turno pode estar no bloco cacheado
    for tok in ("LEDGERMEM", "TIMENOTE", "PRIORITYX"):
        assert tok not in cached["text"]
    tail_parts = content[1:]
    assert "cache_control" not in json.dumps(tail_parts), "o tail per-turno não pode ser cacheado"
    tail = "".join(p["text"] for p in tail_parts)
    for tok in ("LEDGERMEM", "TIMENOTE", "PRIORITYX"):
        assert tok in tail
    # prioridade (extra_system) é a ÚLTIMA coisa lida pelo modelo
    assert tail.index("PRIORITYX") > tail.index("LEDGERMEM")
    assert tail.index("PRIORITYX") > tail.index("TIMENOTE")


def test_non_cache_provider_plain_string_priority_last():
    msg = _system_message("STATIC", "MEM", "deepseek/x", "TIME", "PRIORITY")
    content = msg["content"]
    assert isinstance(content, str)
    assert content.startswith("STATIC")
    assert content.index("PRIORITY") > content.index("MEM")
    assert content.index("PRIORITY") > content.index("TIME")


def test_no_tail_keeps_prefix_only():
    msg = _system_message("ONLYSTATIC", "", "deepseek/x", "", None)
    assert msg["content"] == "ONLYSTATIC"
