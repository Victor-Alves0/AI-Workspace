"""Automações: agendadas (por tempo) e monitores (por evento).

- `runner`: executa uma automação (roda o modelo / checa o watcher) reusando o
  pipeline de chat (`run_turn`).
- `scheduler`: task asyncio única que dispara as automações vencidas.
"""
