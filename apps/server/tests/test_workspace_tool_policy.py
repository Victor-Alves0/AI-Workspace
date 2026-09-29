"""Política de ferramentas do espaço de trabalho num chat COMUM (sem projeto).

Caso real: pediram "um gráfico de linha da alta do Bitcoin nos últimos 6 meses" e o
modelo, com code.exec.run/code.files.* sempre fixados e uma diretiva mandando
"baixar/rodar/buildar", listou a pasta do sandbox, escreveu script e rodou Python 5x —
sem responder — até o usuário mandar usar "a ferramenta do chat" (chart.render.plot).

Política: o sandbox fica DORMENTE (liberado, achado via search_tools, nada fixo) até a
conversa precisar de arquivos/execução; os visuais nativos são apontados como o caminho
para gráficos/diagramas. Chat de PROJETO continua fixando tudo.
"""
from __future__ import annotations

import asyncio
import functools
import uuid
from types import SimpleNamespace as NS

import pytest

from aiworkspace.chat import orchestrator as orch
from aiworkspace.chat.turn_setup import _workspace_intent
from aiworkspace.tools import loader, sift_service

_WS_PINS = {"code__exec__run", "code__files__browse", "code__files__write"}


_REGISTRADAS = {
    "web.search.query", "web.page.read", "chart.render.plot",
    "code.exec.run", "code.files.browse", "code.files.write", "code.graph.query",
    "code.flow.analyze", "code.exec.jobs", "code.preview.serve", "code.task.manage",
    "task.ledger.track", "http.session.use",
}


@functools.lru_cache(maxsize=1)
def _sift_pequena():
    from sift import Sift

    s = Sift()
    sift_service._register_builtins(s, sift_service.SearchConfig(), set(_REGISTRADAS))
    s.build_index()
    return s


class _DB:
    async def scalars(self, _q):
        return []


@pytest.fixture
def sift_de(monkeypatch):
    async def _cfgs(*_a, **_k):
        # sem integrações: só a config de busca (obrigatória) — o resto None
        return (sift_service.search_config_from_secrets(None, None, None), *([None] * 15))

    monkeypatch.setattr(loader, "_assemble_configs", _cfgs)
    # SIFT real, mas só com as tools que o teste enxerga (o catálogo inteiro leva ~80s
    # para indexar); o loader faz scope/pins de verdade sobre ela
    monkeypatch.setattr(sift_service, "get_user_sift", lambda *_a, **_k: _sift_pequena())

    def _build(*, workspace=True, active=None, project=None, pinned=None,
               tool_ids=("builtin:web.search.query", "builtin:chart.render.plot")):
        mc = NS(tools_enabled=True, tool_ids=list(tool_ids), code_mode=False,
                sift_config={"pinned": list(pinned or [])})
        return asyncio.run(loader.get_sift_for_user(
            _DB(), uuid.uuid4(), mc, codespace_project_id=project,
            workspace=workspace, workspace_active=active))
    return _build


def _nomes(scope) -> set[str]:
    return {(t.get("function") or {}).get("name") for t in scope.openai_tools()}


# ------------------------------- loader ------------------------------------ #
def test_chat_comum_com_sandbox_dormente_nao_fixa_tools_de_codigo(sift_de):
    s = sift_de(active=False)
    assert not (_WS_PINS & _nomes(s)), _nomes(s)
    assert s.meta["workspace"] is True and s.meta["workspace_active"] is False


def test_sandbox_dormente_continua_liberado_para_descoberta(sift_de):
    """Nada some: o modelo acha code.exec.run pelo search_tools quando precisar."""
    s = sift_de(active=False)
    raiz = str(s.dispatch("search_tools", {"query": "run a shell command in the sandbox"}))
    assert "\ncode|" in raiz, raiz  # a categoria aparece na descoberta
    fundo = str(s.dispatch("search_tools", {"path": "code.exec"}))
    assert "code.exec.run" in fundo, fundo


def test_sandbox_ativo_fixa_so_as_essenciais(sift_de):
    s = sift_de(active=True)
    nomes = _nomes(s)
    assert _WS_PINS <= nomes
    assert "code__preview__serve" not in nomes  # o resto segue sob demanda
    assert s.meta["workspace_active"] is True


def test_pin_manual_do_editor_ativa_o_sandbox(sift_de):
    """O dono fixou code.exec.run no modelo: vale a escolha dele, e a diretiva completa."""
    s = sift_de(active=False, pinned=["builtin:code.exec.run"])
    assert "code__exec__run" in _nomes(s)
    assert s.meta["workspace_active"] is True


def test_chamador_legado_sem_decisao_mantem_sandbox_ativo(sift_de):
    """Subagentes (operários de código) não passam workspace_active: seguem fixando."""
    s = sift_de(active=None)
    assert _WS_PINS <= _nomes(s)


def test_chat_de_projeto_segue_fixando_todas_as_tools_de_codigo(sift_de):
    s = sift_de(workspace=False, project=str(uuid.uuid4()), active=False)
    nomes = _nomes(s)
    assert {"code__graph__query", "code__preview__serve", "code__exec__run"} <= nomes
    assert s.meta["codespace"] is True


def test_visuais_nativos_liberados_vao_para_o_meta(sift_de):
    assert sift_de(active=False).meta["native_visuals"] == ["chart.render.plot"]
    sem = sift_de(active=False, tool_ids=("builtin:web.search.query",))
    assert sem.meta["native_visuals"] == []


# ---------------------------- intenção (turno) ------------------------------ #
@pytest.mark.parametrize("texto", [
    "crie um gráfico de linha mostrando a alta do Bitcoin nos últimos 6 meses",
    "faça uma tabela comparando iPhone e Galaxy",
    "resuma esse artigo pra mim",
    "a pressão está baixa hoje?",
    "make a bar chart of GDP by country",
])
def test_pedido_de_apresentacao_nao_ativa_o_sandbox(texto):
    assert _workspace_intent(texto) is False


@pytest.mark.parametrize("texto", [
    "clone https://github.com/foo/bar e rode os testes",
    "baixe o programa e execute",
    "roda esse script python",
    "instale o pandas e compile o projeto",
    "run pytest on the repo",
    "abre o main.py e corrige o bug",
])
def test_pedido_de_arquivos_ou_execucao_ativa_o_sandbox(texto):
    assert _workspace_intent(texto) is True


def test_anexo_de_codigo_ou_pacote_ativa_o_sandbox():
    assert _workspace_intent("olha isso", [{"type": "file", "name": "app.zip"}]) is True
    assert _workspace_intent("olha isso", [{"type": "image", "name": "foto.png"}]) is False


# ----------------------------- orchestrator -------------------------------- #
class _FakeSift:
    system_prompt = "SYS"
    code_system_prompt = "CODE-SYS"

    def __init__(self, **meta):
        self.meta = {"catalog": [], "sift_mode": "prompt", "sift_prompt": "", **meta}

    def openai_tools(self):
        return []

    def code_tools(self):
        return []


def _prompt(**meta) -> str:
    return orch._assemble_tools_and_prompt(
        sift=_FakeSift(**meta), use_tools=True, code_mode=False, skills=[], genimage=None,
        kb_tool_on=False, kb_present=False, brain=None, skill_learning=False,
        subagents=[], run_subagent=None, native=orch.NativeToolOpts(),
    ).sift_prompt


def test_sandbox_dormente_ganha_so_o_aviso_curto_e_nao_a_diretiva_de_baixar_rodar():
    p = _prompt(workspace=True, workspace_active=False)
    assert orch.WORKSPACE_DORMANT_NOTE in p
    assert orch.WORKSPACE_DIRECTIVE not in p
    # o aviso ainda impede o "não consigo rodar" (alucinação de capacidade)
    assert "never say you cannot run" in p and "search_tools" in p
    # e o guard de ação real segue sempre presente
    assert orch.TOOL_ACTION_GUARD in p


def test_sandbox_ativo_ganha_a_diretiva_completa_restrita_a_arquivos_e_execucao():
    p = _prompt(workspace=True, workspace_active=True)
    assert orch.WORKSPACE_DIRECTIVE in p
    assert "ONLY when the task genuinely needs files" in p
    assert "charts or diagrams" in p


def test_chat_de_projeto_segue_com_a_postura_de_agente_de_codigo():
    p = _prompt(codespace=True, workspace=False)
    assert orch.CODESPACE_AGENT_DIRECTIVE in p
    assert orch.WORKSPACE_DORMANT_NOTE not in p


def test_grafico_nativo_e_apontado_como_caminho_para_pedido_de_grafico():
    p = _prompt(workspace=True, workspace_active=False, native_visuals=["chart.render.plot"])
    assert "`chart.render.plot`" in p and "execute_tool" in p
    assert "Never build charts by writing or running code" in p


def test_sem_tool_de_grafico_liberada_nao_ha_nota_de_visuais():
    assert "VISUALS" not in _prompt(workspace=True, workspace_active=False)

