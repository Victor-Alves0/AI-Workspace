"""Singularity AI backend package."""

from pathlib import Path

# A versão vem da FONTE ÚNICA (`version` do pyproject.toml), nunca de uma cópia literal
# aqui: esta string é comparada com a tag do último release do GitHub na checagem de
# atualização (admin_routes.update_check / settings_routes.about). Quando era literal ela
# ficou parada em 0.1.0 enquanto os releases iam a 0.5.0 — e o painel dizia "há
# atualização disponível" PARA SEMPRE, inclusive logo depois de atualizar.
#
# 1) pacote instalado (imagem Docker: `pip install .`) → metadados;
# 2) rodando do código-fonte → lê o pyproject.toml ao lado.
# Sincronize os arquivos do desktop com `python scripts/set_version.py <versão>`;
# tests/test_version_sync.py falha se algum divergir.


def _read_version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version("aiworkspace-server")
        except PackageNotFoundError:
            pass
    except ImportError:  # pragma: no cover
        pass
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            if line.startswith("version"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:  # pragma: no cover
        pass
    return "0.0.0+desconhecida"


__version__ = _read_version()
