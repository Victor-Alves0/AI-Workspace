"""Contexto do turno visível às ferramentas (via contextvars).

As tools SIFT rodam num thread do pool (`run_in_threadpool`), que herda o
`contextvars.Context` do turno atual. Isto permite que uma tool descubra o chat
em que foi chamada (ex.: o lembrete "no chat atual") sem precisar plumbar o
chat_id por toda a cadeia de registro/cache da SIFT (que é por-usuário).
"""

from __future__ import annotations

import contextvars

current_chat_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_chat_id", default=None
)

# Fuso IANA do usuário no turno (ex.: "America/Sao_Paulo"), vindo do navegador.
# Vazio = desconhecido → tratar como UTC. Usado por lembretes e eventos de agenda
# para interpretar horários no fuso local do usuário, não no do servidor (UTC).
user_tz: contextvars.ContextVar[str] = contextvars.ContextVar("user_tz", default="")

# True quando o turno roda de forma AUTÔNOMA (automação agendada), sem um usuário
# assistindo. Ferramentas que pedem revisão interativa (ex.: rascunho de e-mail
# para o usuário editar/confirmar) devem PULAR essa fase e agir direto.
background: contextvars.ContextVar[bool] = contextvars.ContextVar("background", default=False)
