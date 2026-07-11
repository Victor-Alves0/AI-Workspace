"""Playground — ambiente de experimentação e medição.

Três modalidades, todas por-usuário:
  - **runner**  → Benchmarks: roda suítes de casos contra 1+ modelos, mede
    latência/tokens/custo e pontua por regra e/ou modelo-juiz. Persiste histórico.
  - **compare** → Comparações: mesmo prompt em N modelos, streaming lado a lado. Efêmero.
  - **debug**   → Debug de Tools: dispatch direto de uma tool + trace de um turno com
    ferramentas. Efêmero.

Reusa os motores do chat (`_resolve_provider`, `openrouter.complete_verbose`/`stream_chat`,
`run_turn`, `get_user_sift`) — não reinventa a chamada de modelo nem o SIFT.
"""
