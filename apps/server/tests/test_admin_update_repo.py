"""Repositório da seção Atualização: normalização para `owner/repo`.

O botão "Padrão" da UI preenche `Victor-Alves0/AI-Workspace`, mas o admin também cola a
URL do navegador. A API do GitHub usa /repos/{owner}/{repo}, então a URL crua montava
`/repos/https://github.com/owner/repo/releases/latest` → 404 sem explicação.
"""
from __future__ import annotations

from aiworkspace.admin_routes import _normalize_repo, _same_commit

_ESPERADO = "Victor-Alves0/AI-Workspace"


def test_normaliza_as_formas_que_o_admin_cola():
    for entrada in [
        _ESPERADO,                                                  # já correto
        "https://github.com/Victor-Alves0/AI-Workspace",            # URL do navegador
        "https://github.com/Victor-Alves0/AI-Workspace/",           # com barra final
        "https://github.com/Victor-Alves0/AI-Workspace.git",        # URL de clone
        "http://github.com/Victor-Alves0/AI-Workspace",             # http
        "github.com/Victor-Alves0/AI-Workspace",                    # sem esquema
        "www.github.com/Victor-Alves0/AI-Workspace",                # com www
        "git@github.com:Victor-Alves0/AI-Workspace.git",            # SSH
        "https://github.com/Victor-Alves0/AI-Workspace/tree/main",  # caminho extra
        "  https://github.com/Victor-Alves0/AI-Workspace  ",        # espaços
    ]:
        assert _normalize_repo(entrada) == _ESPERADO, entrada


def test_entradas_vazias_ou_parciais_nao_quebram():
    assert _normalize_repo("") == ""
    assert _normalize_repo("   ") == ""
    assert _normalize_repo(None) == ""          # type: ignore[arg-type]
    # só o dono (sem repo): devolve como veio — o update-check então dá 404 explicado,
    # em vez de estourar aqui
    assert _normalize_repo("Victor-Alves0") == "Victor-Alves0"


def test_compara_commit_por_prefixo_comum():
    """A imagem guarda um short hash e a API do GitHub devolve o sha completo — a
    comparação tem que ser por prefixo, senão NUNCA bateria e o painel diria
    'há commits novos' para sempre."""
    curto, completo = "24db3c7a1b2c", "24db3c7a1b2c3d4e5f60718293a4b5c6d7e8f900"
    assert _same_commit(curto, completo) is True
    assert _same_commit(completo, curto) is True          # ordem não importa
    assert _same_commit("24db3c7a", completo) is True     # 8 chars também casa
    assert _same_commit("AABBCCDD", "aabbccddeeff") is True  # case-insensitive


def test_commit_diferente_e_desconhecido():
    completo = "24db3c7a1b2c3d4e5f60718293a4b5c6d7e8f900"
    assert _same_commit("deadbeef", completo) is False
    # None = não dá p/ afirmar nada (imagem sem GIT_COMMIT, ou GitHub sem responder).
    # Importa que seja None e NÃO False: False acusaria "atrasado" sem saber.
    assert _same_commit("", completo) is None
    assert _same_commit(None, completo) is None
    assert _same_commit(completo, "") is None
    assert _same_commit("   ", completo) is None


def test_url_normalizada_monta_o_endpoint_certo_da_api():
    """O contrato real: o valor normalizado entra em /repos/{repo}/… sem quebrar."""
    repo = _normalize_repo("https://github.com/Victor-Alves0/AI-Workspace")
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    assert url == "https://api.github.com/repos/Victor-Alves0/AI-Workspace/releases/latest"
    assert "github.com/https" not in url
