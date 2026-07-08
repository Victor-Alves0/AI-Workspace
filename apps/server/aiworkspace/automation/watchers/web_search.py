"""WebSearchWatcher — dispara quando surgem RESULTADOS novos para uma busca,
opcionalmente filtrados por uma condição em linguagem natural.

config: {
  "query": "placar Brasil hoje",   # obrigatório
  "condition": "o jogo terminou"   # opcional: o LLM só confirma o disparo se a
                                    #   condição for satisfeita pelos novos resultados
}
Compara o conjunto de URLs com o estado anterior; o LLM (barato) só é chamado
quando há resultado novo E há condição — mantendo o custo baixo.
"""

from __future__ import annotations


async def check(config: dict, state: dict, deps) -> dict:
    query = (config.get("query") or "").strip()
    if not query:
        return {"changed": False, "new_state": state, "error": "informe a busca (query)"}
    if deps.web_search is None:
        return {"changed": False, "new_state": state, "error": "busca na web indisponível"}

    try:
        results = await deps.web_search(query)
    except Exception as exc:  # noqa: BLE001
        return {"changed": False, "new_state": state, "error": f"falha na busca: {exc}"}

    results = results or []
    urls = [r.get("url") for r in results if r.get("url")]
    prev = set(state.get("urls") or [])
    new_state = {"urls": urls[:50]}

    # primeira observação: linha de base
    if not state.get("urls"):
        return {"changed": False, "new_state": new_state}

    new_results = [r for r in results if r.get("url") and r["url"] not in prev]
    if not new_results:
        return {"changed": False, "new_state": new_state}

    corpus = "\n".join(
        f"- {r.get('title','')} ({r.get('url','')}): {(r.get('content') or '')[:200]}"
        for r in new_results[:6]
    )

    condition = (config.get("condition") or "").strip()
    # sem condição: qualquer resultado novo já dispara
    if not condition or deps.llm is None:
        return {"changed": True, "summary_input": f'Novidades para "{query}":\n{corpus}', "new_state": new_state}

    # com condição: o LLM confirma se algum resultado novo a satisfaz
    verdict = await deps.llm(
        'You check whether a condition is satisfied by search results. '
        'Answer ONLY with "YES" or "NO" on the first line.',
        f"Condition: {condition}\n\nNew results:\n{corpus}",
        60,
    )
    met = (verdict or "").strip().upper().startswith("YES")
    if not met:
        return {"changed": False, "new_state": new_state}
    return {
        "changed": True,
        "summary_input": f'Condição atendida ("{condition}") para "{query}":\n{corpus}',
        "new_state": new_state,
    }
