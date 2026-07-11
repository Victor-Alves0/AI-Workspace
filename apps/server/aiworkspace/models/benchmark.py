"""Playground — Benchmarks: suítes de casos de teste + histórico de execuções.

Um **benchmark** é uma coleção de casos (prompt + esperado/critério de juiz) que o
usuário roda contra 1+ modelos. Cada disparo cria um **benchmark_run** com os
resultados por caso×modelo (saída, latência, tokens, custo, pass/fail da regra e nota
do juiz) e os agregados por modelo. Espelha o padrão de `automation_run.py` (linha por
execução, sobrevive à exclusão de chats).

Comparações e Debug de Tools são efêmeros (não persistem) — só os benchmarks têm
histórico, por isso são as únicas tabelas do Playground.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class Benchmark(Base):
    __tablename__ = "benchmarks"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    # casos de teste: [{id, prompt, system?, expected?:{mode:"none"|"contains"|"regex",
    # value}, judge_criteria?}]
    cases: Mapped[list] = mapped_column(JSONB, default=list)
    # base model do juiz LLM (ex.: "openai/gpt-4o-mini"); vazio/None = sem juiz
    judge_model: Mapped[str | None] = mapped_column(String(255), nullable=True)


class BenchmarkRun(Base):
    __tablename__ = "benchmark_runs"

    benchmark_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("benchmarks.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # modelos testados: [{model, model_config_id?, label}]
    models: Mapped[list] = mapped_column(JSONB, default=list)
    # running | done | error
    status: Mapped[str] = mapped_column(String(16), default="running")
    # resultados por caso×modelo: {"<case_id>|<model_key>": {text, latency_ms,
    # prompt_tokens, completion_tokens, cost, rule_pass?, judge_score?, judge_reason?,
    # error?}}
    results: Mapped[dict] = mapped_column(JSONB, default=dict)
    # agregados por modelo: {"<model_key>": {avg_latency, total_tokens, total_cost,
    # pass_rate, avg_judge, count}}
    aggregates: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
