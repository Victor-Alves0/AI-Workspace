"""Empacota os pacotes só-Python do site-packages num único .zip.

O tempo de instalação do app desktop é dominado pela QUANTIDADE de arquivos (o
instalador e o antivírus tratam um por um): ~18 mil arquivos .py soltos viram um
arquivo. O Python importa direto do zip (zipimport); é o que o py2exe/Calibre fazem
com o library.zip.

Fica FORA do zip (continua solto em site-packages):
  - pacote com binário nativo (.pyd/.dll): o Windows só carrega DLL do disco;
  - KEEP_OUT: quem lê arquivos próprios por caminho do disco (open(__file__/..)),
    e o `aiworkspace`, que a atualização leve do app troca no disco.

Cada .py vai com o .pyc ao lado (hash sem checagem): o zipimport não grava cache,
então sem o .pyc dentro do zip o Python recompilaria esses módulos a cada abertura.

Uso: python pack_site.py <site-packages> <saida.zip>
"""
from __future__ import annotations

import importlib.util
import marshal
import os
import shutil
import sys
import zipfile

NATIVE = (".pyd", ".dll", ".exe", ".so")
KEEP_OUT = {
    "aiworkspace",  # atualização leve troca esta pasta no disco
    "jedi", "parso",  # stubs/gramáticas lidos por caminho
    "docx", "pptx",  # modelos .docx/.pptx por caminho
    "alembic",  # templates por caminho
    "certifi",  # cacert.pem entregue como caminho a httpx/requests
    "win32com", "pythonwin", "adodbapi", "pywin32_system32", "win32", "win32comext",
    "codegraph",  # ts_service.js executado pelo node
    "kokoro_onnx", "phonemizer",  # voz embutida: config.json aberto por caminho; dados g2p
    "pywin32_bootstrap",  # importado pelo pywin32.pth na inicialização
    "cffi", "pycparser",
    "bin", "share", "include",
}


def _top_level(site: str) -> tuple[list[str], list[str]]:
    """(pacotes/módulos que vão para o zip, os que ficam)."""
    vai, fica = [], []
    for nome in sorted(os.listdir(site)):
        caminho = os.path.join(site, nome)
        if nome == "__pycache__" or nome.endswith((".dist-info", ".data", ".pth")):
            continue
        base = nome[:-3] if nome.endswith(".py") else nome
        if os.path.isdir(caminho):
            arquivos = [f for _, _, fs in os.walk(caminho) for f in fs]
            nativo = any(f.endswith(NATIVE) for f in arquivos)
            (fica if nativo or base in KEEP_OUT else vai).append(nome)
        elif nome.endswith(".py"):
            (fica if base in KEEP_OUT else vai).append(nome)
    return vai, fica


def _dist_infos(site: str) -> list[str]:
    """Todos os dist-info vão para o zip: o importlib.metadata procura metadados em
    toda entrada do sys.path (zip incluso), esteja o pacote no zip ou no disco."""
    return [n for n in os.listdir(site) if n.endswith(".dist-info")]


def _pyc(fonte: bytes, arcname: str) -> bytes:
    """.pyc hash-based SEM checagem (PEP 552): o zipimport aceita sem comparar data."""
    codigo = compile(fonte, arcname, "exec", dont_inherit=True)
    flags = 0b01  # hash-based, check_source = 0
    cabecalho = importlib.util.MAGIC_NUMBER + flags.to_bytes(4, "little")
    return cabecalho + importlib.util.source_hash(fonte) + marshal.dumps(codigo)


def pack(site: str, destino: str) -> dict:
    vai, fica = _top_level(site)
    metas = _dist_infos(site)
    n = 0
    pastas: set[str] = set()
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_STORED) as z:
        for nome in vai + metas:
            origem = os.path.join(site, nome)
            if os.path.isfile(origem):
                pares = [(origem, nome)]
            else:
                pares = [
                    (os.path.join(d, f), os.path.relpath(os.path.join(d, f), site).replace("\\", "/"))
                    for d, dirs, fs in os.walk(origem)
                    for f in fs
                    if "__pycache__" not in d
                ]
            for caminho, arc in pares:
                # entrada de diretório explícita: sem ela o zipimport não enxerga
                # subpacote namespace (pasta sem __init__.py, ex.: fastembed.image.transform)
                partes = arc.split("/")[:-1]
                for i in range(1, len(partes) + 1):
                    pasta = "/".join(partes[:i]) + "/"
                    if pasta not in pastas:
                        pastas.add(pasta)
                        z.writestr(pasta, b"")
                dados = open(caminho, "rb").read()
                z.writestr(arc, dados)
                n += 1
                if arc.endswith(".py"):
                    try:
                        z.writestr(arc + "c", _pyc(dados, "site-packages.zip/" + arc))
                    except SyntaxError:
                        pass  # arquivo de exemplo/modelo que não é Python válido
    for nome in vai + metas:
        caminho = os.path.join(site, nome)
        if os.path.isdir(caminho):
            shutil.rmtree(caminho)
        else:
            os.remove(caminho)
    return {"zipados": len(vai), "metadados": len(metas), "arquivos": n, "ficaram": fica}


if __name__ == "__main__":
    r = pack(sys.argv[1], sys.argv[2])
    print(f"==> {r['arquivos']} arquivos de {r['zipados']} pacotes (+{r['metadados']} dist-info) "
          f"em {os.path.basename(sys.argv[2])}; ficam soltos: {', '.join(r['ficaram'])}")
