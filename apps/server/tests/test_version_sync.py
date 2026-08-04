"""A versão do produto tem UMA fonte e todos os arquivos a seguem.

Regressão real: a versão vivia copiada em 5 lugares; `desktop/*` subiram para 0.5.0 e
`pyproject.toml` + `aiworkspace.__version__` ficaram em 0.1.0. Como a checagem de
atualização compara `__version__` com a tag do último release, o painel dizia
"há atualização disponível" PARA SEMPRE — inclusive logo depois de atualizar.

Fonte única: `apps/server/pyproject.toml` ([project].version).
Propagação: `python scripts/set_version.py <versão>`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import aiworkspace

_SERVER = Path(aiworkspace.__file__).resolve().parent.parent   # apps/server
_REPO = _SERVER.parent.parent                                   # raiz do repositório


def _pyproject_version() -> str:
    txt = (_SERVER / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', txt)
    assert m, "pyproject.toml sem campo version"
    return m.group(1)


def test_version_do_pacote_vem_do_pyproject():
    """__version__ DERIVA da fonte — nunca é uma cópia literal que possa envelhecer."""
    assert aiworkspace.__version__ == _pyproject_version()
    # e não caiu no fallback de "não descobri a versão"
    assert "desconhecida" not in aiworkspace.__version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", aiworkspace.__version__), aiworkspace.__version__


def test_arquivos_do_desktop_acompanham_a_fonte():
    """Os 3 arquivos do desktop precisam do valor literal (não leem outro arquivo no
    build) — então o teste é o que garante que não divirjam de novo."""
    if not (_REPO / "desktop").is_dir():
        import pytest
        pytest.skip("árvore do desktop ausente (imagem só do servidor)")
    want = _pyproject_version()
    pkg = json.loads((_REPO / "desktop/package.json").read_text(encoding="utf-8"))
    assert pkg["version"] == want, f"desktop/package.json={pkg['version']!r} != {want!r}"
    conf = json.loads((_REPO / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    assert conf["version"] == want, f"tauri.conf.json={conf['version']!r} != {want!r}"
    cargo = (_REPO / "desktop/src-tauri/Cargo.toml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', cargo)
    assert m and m.group(1) == want, f"Cargo.toml={m and m.group(1)!r} != {want!r}"
