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

# Projeto do Codespace vinculado a ESTE chat (None = nenhum). As tools
# code.graph.query/code.files.browse leem daqui em vez de receber o projeto como
# parâmetro — evita threadar um "codespace_cfg" por toda a cadeia de registro/
# cache da SIFT (que é por-USUÁRIO, não por-chat), igual ao current_chat_id.
current_codespace_project_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_codespace_project_id", default=None
)

# Worktree ATIVO do Codespace (id da CodespaceTask) quando o turno/worker roda dentro
# de uma tarefa isolada. Quando setado, as tools de escrita/exec operam no worktree
# (`<proj>/wt/<task_id>`) em vez do `src`, e commitam na branch do worktree sem push.
# None = trabalha direto no `src` do projeto (comportamento padrão).
current_codespace_worktree: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_codespace_worktree", default=None
)

# Fuso IANA do usuário no turno (ex.: "America/Sao_Paulo"), vindo do navegador.
# Vazio = desconhecido → tratar como UTC. Usado por lembretes e eventos de agenda
# para interpretar horários no fuso local do usuário, não no do servidor (UTC).
user_tz: contextvars.ContextVar[str] = contextvars.ContextVar("user_tz", default="")

# True quando o turno roda de forma AUTÔNOMA (automação agendada), sem um usuário
# assistindo. Ferramentas que pedem revisão interativa (ex.: rascunho de e-mail
# para o usuário editar/confirmar) devem PULAR essa fase e agir direto.
background: contextvars.ContextVar[bool] = contextvars.ContextVar("background", default=False)

# Perfil do usuário do turno (nome, sobre, gênero, nascimento, e-mail, idioma) —
# preenchido pelo run_turn a partir do User. Fica disponível à ferramenta de
# sistema `user.profile.get`, que devolve esses dados quando o modelo precisa
# (ex.: "qual meu nome?", "quantos anos eu tenho?"). Dict vazio = sem dados.
user_profile: contextvars.ContextVar[dict] = contextvars.ContextVar("user_profile", default={})
