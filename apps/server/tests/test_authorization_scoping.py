"""Bateria de ESCOPO DE AUTORIZAÇÃO: pega "resolve recurso por id sem checar dono".

Por que existe (ver docs/trust-model.md): o Singularity AI é multiusuário num único domínio
de confiança. Vazamento entre usuários não é "defesa contra usuário malicioso" — é higiene,
e é a classe de bug MAIS fácil de introduzir sem perceber, porque nada obriga a lembrar.
Aconteceu de verdade: `code.exec.jobs` resolvia `job_id` num dicionário global e devolvia a
saída do comando de OUTRO projeto (corrigido com `_lookup(job_id, project_id)`).

A regra que este teste impõe: toda rota que recebe um id no PATH precisa de evidência de
escopo — helper de posse, comparação com o usuário da sessão, ou ser explicitamente de
admin. Quem for exceção legítima entra na allowlist ABAIXO, com motivo escrito.

Heurística, não prova: usa AST (não casa em comentário/string solta) e erra para o lado de
aceitar. O valor está em falhar no dia em que alguém adicionar uma rota nova sem escopo.
"""
from __future__ import annotations

import ast
import pathlib

import aiworkspace

# localiza o pacote pelo IMPORT (não relativo a este arquivo): assim funciona tanto no
# repo (pytest em apps/server) quanto rodando de /tmp dentro do container.
_PKG = pathlib.Path(aiworkspace.__file__).resolve().parent

# Evidência de que o handler ESCOPA o recurso ao solicitante.
_SCOPE_EVIDENCE = (
    "owned",            # _get_owned_chat, _load_owned, _find_owned, _owned_project…
    "user.id", "user_id", "current_user",
    "ctx.user",         # camada da API pública: o escopo vem do ApiContext da chave
    "require_admin",    # rota de admin: global por definição
    "_cs_project_ctx",  # contexto do projeto do Codespace (já valida dono)
    "port_owned_by",
    "user=user", "owner",
)

# Exceções LEGÍTIMAS: "arquivo::handler" -> motivo. Toda entrada aqui é uma promessa de
# que o recurso não é por-usuário. Manter curta — se crescer, a regra virou decoração.
# As duas primeiras são URL-capacidade: a autorização é o TOKEN ASSINADO no query, não a
# sessão (é o que faz o link funcionar em <img>/<video> sem cookie cross-origin). Quem
# tiver o link vê o recurso — decisão consciente, igual a "qualquer um com o link".
_ALLOWLIST: dict[str, str] = {
    "image_routes.py::get_image": "URL-capacidade: autoriza por token assinado (verify_image_token)",
    "knowledge_routes.py::get_doc_raw": "URL-capacidade: autoriza por token assinado (verify_doc_token)",
    "share_routes.py::get_shared_chat": "link público é a funcionalidade (compartilhamento explícito)",
}

_METHODS = {"get", "post", "put", "patch", "delete"}


def _route_paths(fn: ast.AST) -> list[str]:
    """Paths dos decoradores @router.<método>("/...") deste handler."""
    out = []
    for dec in getattr(fn, "decorator_list", []):
        if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
            continue
        if dec.func.attr not in _METHODS:
            continue
        for arg in dec.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                out.append(arg.value)
    return out


def _handlers_with_id_param(path: pathlib.Path):
    """(nome, linha, rota) de cada handler cujo PATH tem um {…id}."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for route in _route_paths(node):
            if "{" in route and "id}" in route:
                yield node, route
                break


def test_rotas_com_id_no_path_checam_dono():
    """Toda rota com {…id} no path escopa o recurso (posse, usuário ou admin)."""
    violations: list[str] = []
    files = sorted(_PKG.rglob("*routes.py"))
    assert files, "não achei os módulos de rotas — caminho errado?"
    checked = 0
    for f in files:
        rel = f.relative_to(_PKG).as_posix()
        for node, route in _handlers_with_id_param(f):
            key = f"{rel}::{node.name}"
            if key in _ALLOWLIST:
                continue
            checked += 1
            src = ast.get_source_segment(f.read_text(encoding="utf-8"), node) or ""
            if not any(ev in src for ev in _SCOPE_EVIDENCE):
                violations.append(f"{rel}:{node.lineno} {node.name}() rota {route}")
    assert checked > 50, f"scanner cobriu poucas rotas ({checked}) — regressão do próprio teste"
    assert not violations, (
        "rota(s) com id no path SEM evidência de escopo de dono — adicione a checagem de "
        "posse (ex.: _get_owned_chat) ou, se o recurso for público de propósito, registre "
        "em _ALLOWLIST com o motivo:\n  " + "\n  ".join(violations)
    )


def test_registros_em_memoria_exigem_escopo():
    """Registros por-processo (dict global) resolvem por id — o escopo tem que ser
    PARÂMETRO, senão um id de outro projeto/usuário devolve o recurso. Foi exatamente o
    bug do `code.exec.jobs` (saída de comando de outro projeto)."""
    import inspect

    from aiworkspace.codespace import exec_jobs, preview_service

    # exec_jobs: status/wait aceitam project_id p/ escopar
    for fn in (exec_jobs.job_status, exec_jobs.wait_job):
        params = inspect.signature(fn).parameters
        assert "project_id" in params, f"{fn.__name__} precisa aceitar project_id (escopo)"
    assert "project_id" in inspect.signature(exec_jobs._lookup).parameters

    # o escopo tem que ser EFETIVO, não só existir na assinatura
    assert exec_jobs.job_status("inexistente", project_id="pX").get("error")

    # preview: a resolução por id/porta passa pelo dono
    for name in ("_find_owned", "port_owned_by"):
        assert hasattr(preview_service, name), f"preview_service.{name} sumiu — escopo perdido"
    assert "user_id" in inspect.signature(preview_service.port_owned_by).parameters


def test_memoria_update_delete_exigem_dono():
    """REGRESSÃO (achado por esta bateria): `mem0.update/delete` endereçam o vector store
    por id GLOBAL. As rotas passavam só o `api_key` — que é a chave do LLM (vira "x" p/
    quem não tem chave!), NÃO um escopo — então um memory_id de OUTRO usuário era
    aceito e alterado/apagado. Agora exigem `owner_user_id` e falham FECHADO."""
    import inspect

    from aiworkspace.memory import mem0_service

    for fn in (mem0_service.update_memory, mem0_service.delete_memory):
        assert "owner_user_id" in inspect.signature(fn).parameters, fn.__name__

    class _FakeMem:
        """memória do usuário 'dono-A'."""
        def __init__(self):
            self.updated = self.deleted = False
        def get(self, mid):
            return {"id": mid, "memory": "x", "user_id": "dono-A"}
        def update(self, mid, text):
            self.updated = True
        def delete(self, mid):
            self.deleted = True

    fake = _FakeMem()
    orig = mem0_service._memory_for_key
    mem0_service._memory_for_key = lambda key: fake
    try:
        # dono correto → passa
        assert mem0_service.update_memory("k", "m1", "novo", "dono-A") is True
        assert fake.updated
        # OUTRO usuário → recusa e NÃO toca na memória
        fake.updated = fake.deleted = False
        assert mem0_service.update_memory("k", "m1", "hack", "invasor-B") is False
        assert mem0_service.delete_memory("k", "m1", "invasor-B") is False
        assert not fake.updated and not fake.deleted
        # sem escopo informado → falha fechado (não assume o antigo "passa direto")
        assert mem0_service.delete_memory("k", "m1", "") is False
        assert not fake.deleted
    finally:
        mem0_service._memory_for_key = orig


def test_proxy_de_preview_e_autenticado():
    """O proxy do preview (/codespace/preview/<porta>/) é o único caminho AUTENTICADO até
    o app — a porta publicada não tem auth. Se alguém remover a checagem de posse daqui,
    qualquer usuário logado alcança o preview de outro."""
    import inspect

    from aiworkspace import codespace_routes

    src = inspect.getsource(codespace_routes.preview_proxy)
    assert "port_owned_by" in src, "o proxy do preview precisa checar posse da porta"
    src_ws = inspect.getsource(codespace_routes.preview_ws)
    assert "port_owned_by" in src_ws, "o proxy WebSocket do preview também precisa checar posse"
