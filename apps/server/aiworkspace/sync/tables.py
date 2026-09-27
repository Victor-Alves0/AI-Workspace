"""O que a sincronização replica — derivado dos MODELOS, não de uma lista à mão.

Toda tabela nova entra na sync automaticamente (é dado do usuário), a não ser que
esteja em `EXCLUDED`. A exclusão é para o que é da INSTÂNCIA ou perigoso em dobro:
  - automações: rodariam nas duas instâncias (e-mail mandado duas vezes);
  - bots de canal (WhatsApp/Telegram/Discord/Slack): responderiam duas vezes;
  - Codespace: os arquivos vivem em disco, fora do banco;
  - telemetria, auditoria, config global, chaves da API pública, push do navegador,
    jobs em andamento — são desta máquina.
Colunas que apontam para uma tabela excluída (ex.: `chats.project_id`) NÃO viajam:
cada lado guarda o seu valor.

Dono de cada linha: a coluna `user_id`; nas filhas sem ela (mensagens, versões de
artefato, compactações) o dono vem do pai. Só as contas em comum (mesmo e-mail nas
duas instâncias) são sincronizadas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

EXCLUDED = frozenset({
    "users",  # não viaja: as contas são ligadas pelo e-mail (user_map)
    "app_settings", "api_keys", "api_requests", "audit_events", "health_events",
    "obs_traces", "obs_spans", "push_subscriptions", "media_jobs",
    "automations", "automation_runs", "notifications",
    "codespace_projects", "codespace_tasks", "codespace_exec_jobs",
    "discord_connections", "discord_threads",
    "telegram_connections", "telegram_threads",
    "slack_channel_connections", "slack_channel_threads",
    "whatsapp_connections", "whatsapp_chats", "whatsapp_messages", "whatsapp_threads",
    "sync_peers",
})

# tabelas fora do ORM (SQL cru) que também são dado do usuário
RAW_MEMORIES = "aiworkspace_memories"

# filhas sem user_id: o dono vem do pai por esta coluna
_PARENT_OWNER = {
    "messages": ("chat_id", "chats"),
    "artifact_versions": ("artifact_id", "artifacts"),
    "chat_compactions": ("chat_id", "chats"),
}


@dataclass
class TableInfo:
    name: str
    columns: frozenset[str]
    user_cols: tuple[str, ...] = ()          # FKs para users.id (remapeadas por e-mail)
    dropped_cols: tuple[str, ...] = ()       # FKs para tabelas fora da sync
    encrypted_cols: tuple[str, ...] = ()     # EncryptedText ("enc:v1:…")
    raw_fernet_cols: tuple[str, ...] = ()    # token Fernet cru (user_secrets.ciphertext)
    json_cols: tuple[str, ...] = ()          # podem conter tokens Fernet dentro
    parent: tuple[str, str] | None = None    # (coluna, tabela) de quem herda o dono
    natural_keys: tuple[tuple[str, ...], ...] = ()  # UNIQUE (user_id, x): mesma coisa nos dois lados
    fk_refs: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # (tabela filha, coluna) que apontam p/ cá


@lru_cache
def synced() -> dict[str, TableInfo]:
    """Tabelas replicadas, na ORDEM de dependência (pais antes dos filhos)."""
    import sqlalchemy as sa
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.types import JSON

    from .. import models  # noqa: F401 - registra todas as tabelas
    from ..crypto import EncryptedText
    from ..db import Base

    tables = {t.name: t for t in Base.metadata.tables.values() if t.name not in EXCLUDED}
    infos: dict[str, TableInfo] = {}
    for name, t in tables.items():
        user_cols, dropped = [], []
        for fk in t.foreign_keys:
            alvo = fk.column.table.name
            if alvo == "users":
                user_cols.append(fk.parent.name)
            elif alvo not in tables:
                dropped.append(fk.parent.name)
        enc = [c.name for c in t.columns if isinstance(c.type, EncryptedText)]
        js = [c.name for c in t.columns if isinstance(c.type, (JSON, JSONB))]
        naturais = []
        for u in t.constraints:
            if isinstance(u, sa.UniqueConstraint):
                cols = tuple(c.name for c in u.columns)
                if "user_id" in cols and len(cols) > 1:
                    naturais.append(cols)
        infos[name] = TableInfo(
            name=name,
            columns=frozenset(c.name for c in t.columns),
            user_cols=tuple(sorted(user_cols)),
            dropped_cols=tuple(sorted(dropped)),
            encrypted_cols=tuple(enc),
            raw_fernet_cols=("ciphertext",) if name == "user_secrets" else (),
            json_cols=tuple(js),
            parent=_PARENT_OWNER.get(name),
            natural_keys=tuple(naturais),
        )
    # quem aponta para cada tabela (p/ re-chavear uma linha sem quebrar as filhas) —
    # inclusive as tabelas FORA da sync (uma automação aponta para o modelo)
    refs: dict[str, list[tuple[str, str]]] = {n: [] for n in infos}
    for name, t in Base.metadata.tables.items():
        for fk in t.foreign_keys:
            alvo = fk.column.table.name
            if alvo in refs:
                refs[alvo].append((name, fk.parent.name))
    for n, info in infos.items():
        info.fk_refs = tuple(sorted(refs[n]))

    # ordem topológica (autorreferência não conta)
    deps = {n: {fk.column.table.name for fk in tables[n].foreign_keys
                if fk.column.table.name in infos and fk.column.table.name != n}
            for n in infos}
    ordem: list[str] = []
    feitos: set[str] = set()
    while len(ordem) < len(infos):
        prontos = sorted(n for n in infos if n not in feitos and deps[n] <= feitos)
        if not prontos:  # ciclo entre tabelas: segue pela ordem alfabética
            prontos = sorted(n for n in infos if n not in feitos)[:1]
        for n in prontos:
            ordem.append(n)
            feitos.add(n)
    out = {n: infos[n] for n in ordem}
    out[RAW_MEMORIES] = TableInfo(
        name=RAW_MEMORIES, columns=frozenset({"id", "vector", "payload"}), json_cols=("payload",))
    return out


def order_index() -> dict[str, int]:
    return {n: i for i, n in enumerate(synced())}
