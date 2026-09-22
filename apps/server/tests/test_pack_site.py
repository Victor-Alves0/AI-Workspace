"""Instalador do desktop: pacotes só-Python num único site-packages.zip.

O tempo de instalação é dominado pela quantidade de arquivos; o build junta ~10 mil
.py num zip de onde o Python importa direto (desktop/engine/pack_site.py).
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import zipfile
from pathlib import Path

_PACK = Path(__file__).resolve().parents[3] / "desktop" / "engine" / "pack_site.py"


def _carregar():
    spec = importlib.util.spec_from_file_location("pack_site", _PACK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _site(tmp_path: Path) -> Path:
    site = tmp_path / "site-packages"
    # pacote só-Python, com subpacote NAMESPACE (sem __init__.py) e um arquivo de dados
    (site / "puro" / "ns").mkdir(parents=True)
    (site / "puro" / "__init__.py").write_text("VALOR = 1\n")
    (site / "puro" / "ns" / "mod.py").write_text("X = 'namespace'\n")
    (site / "puro" / "dados.txt").write_text("conteudo")
    (site / "puro-1.0.dist-info").mkdir()
    (site / "puro-1.0.dist-info" / "METADATA").write_text("Name: puro\nVersion: 1.0\n")
    (site / "modulo_solto.py").write_text("Y = 2\n")
    # nativo: fica no disco (o Windows só carrega DLL de arquivo)
    (site / "nativo").mkdir()
    (site / "nativo" / "__init__.py").write_text("")
    (site / "nativo" / "_ext.pyd").write_bytes(b"MZ")
    # exceção: lê arquivos próprios por caminho / a atualização leve troca no disco
    (site / "aiworkspace").mkdir()
    (site / "aiworkspace" / "__init__.py").write_text("")
    (site / "algo.pth").write_text("")
    return site


def test_so_python_vai_para_o_zip_e_o_resto_fica_no_disco(tmp_path):
    site = _site(tmp_path)
    destino = tmp_path / "site-packages.zip"

    r = _carregar().pack(str(site), str(destino))

    soltos = sorted(p.name for p in site.iterdir())
    assert soltos == ["aiworkspace", "algo.pth", "nativo"]
    nomes = zipfile.ZipFile(destino).namelist()
    assert "puro/__init__.pyc" in nomes            # .pyc ao lado: sem recompilar a cada abertura
    assert "puro/ns/" in nomes                     # entrada de diretório do subpacote namespace
    assert "puro-1.0.dist-info/METADATA" in nomes  # importlib.metadata acha dentro do zip
    assert r["zipados"] == 2


def test_python_importa_de_dentro_do_zip(tmp_path):
    """Sem a entrada de diretório, `puro.ns.mod` dava ModuleNotFoundError
    (aconteceu com fastembed.image.transform)."""
    site = _site(tmp_path)
    destino = tmp_path / "site-packages.zip"
    _carregar().pack(str(site), str(destino))

    codigo = (
        "import sys; sys.path[:0] = [sys.argv[1]]\n"
        "import puro, puro.ns.mod, modulo_solto\n"
        "from importlib.metadata import version\n"
        "from importlib.resources import files\n"
        "assert puro.__spec__.loader.__class__.__name__ == 'zipimporter'\n"
        "assert puro.ns.mod.X == 'namespace' and modulo_solto.Y == 2\n"
        "assert version('puro') == '1.0'\n"
        "assert files('puro').joinpath('dados.txt').read_text() == 'conteudo'\n"
        "print('ok')\n"
    )
    saida = subprocess.run([sys.executable, "-S", "-c", codigo, str(destino)],
                           capture_output=True, text=True, timeout=60)
    assert saida.stdout.strip() == "ok", saida.stderr
