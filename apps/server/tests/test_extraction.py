"""Extração de texto dos anexos: o que o modelo realmente recebe.

O caso que deu origem a estes testes: o usuário colou um texto longo, o compositor
transformou em `Texto colado ….txt` (como o ChatGPT faz) e a IA respondeu que o
arquivo "não tem texto extraível" — porque a extração cobria PDF/Word/Excel/PPT/CSV
e **nenhum formato de texto puro**. O anexo mais comum de todos era o único que não
chegava ao modelo.

Herméticos: bytes em memória, nenhum parser externo além dos já usados em produção.
"""
from __future__ import annotations

import pytest

from aiworkspace.extraction import ExtractionError, extract, is_extractable, kind_of

NUL = bytes([0])


# --------------------------------------------------------------------------- #
# Texto puro                                                                   #
# --------------------------------------------------------------------------- #
def test_texto_colado_chega_inteiro_ao_modelo():
    """A regressão em uma linha: colar texto longo vira um .txt, e o .txt tem que
    ser legível. Antes disto o modelo recebia "[anexo sem texto extraível]"."""
    conteudo = "primeira linha\nsegunda linha\n"
    nome = "Texto colado 20/09/2026, 14:03.txt"

    assert is_extractable(nome, "text/plain")
    assert extract(nome, "text/plain", conteudo.encode()) == conteudo


def test_codigo_mantem_a_indentacao():
    """Texto puro NÃO passa pelo colapso de espaços. Colapsar economiza tokens num
    PDF, mas em código/markdown/log destrói justamente a informação: a indentação
    some e o modelo passa a ler um programa que não compila."""
    codigo = "def f(x):\n    if x:\n        return 1\n    return 0\n"

    saida = extract("script.py", "text/x-python", codigo.encode(), {"collapse_whitespace": True})

    assert saida == codigo


@pytest.mark.parametrize(
    "nome,mime",
    [
        ("notas.md", "text/markdown"),
        ("dados.json", "application/json"),
        ("config.yml", ""),
        ("servidor.log", "text/plain"),
        ("pagina.html", "text/html"),
        ("componente.tsx", ""),
        ("consulta.sql", ""),
    ],
)
def test_formatos_de_texto_reconhecidos(nome, mime):
    """O compositor aceita todos estes no seletor de arquivos; se o servidor não os
    reconhecer, o usuário anexa e o modelo não vê nada."""
    assert kind_of(nome, mime) == "text"


def test_csv_nao_e_engolido_pelo_texto_puro():
    """`text/csv` casa com o prefixo "text/", então a ORDEM em _KINDS importa: o CSV
    tem parser próprio (corte por linhas) e precisa vencer o ramo genérico."""
    linhas = "a,b\n" + "".join(f"{i},{i}\n" for i in range(50))

    assert kind_of("tabela.csv", "text/csv") == "csv"
    saida = extract("tabela.csv", "text/csv", linhas.encode(), {"xlsx_max_rows": 5})
    assert saida.count("\n") <= 6  # cabeçalho + 5 linhas


def test_binario_disfarcado_de_texto_e_recusado():
    """Um .txt que na verdade é binário (arquivo renomeado, .doc antigo) despejaria
    lixo no contexto. Recusar vira um aviso no anexo — informação útil — em vez de
    milhares de tokens ilegíveis."""
    with pytest.raises(ExtractionError):
        extract("falso.txt", "text/plain", b"PK\x03\x04" + NUL + b"conteudo")


def test_acentuacao_fora_de_utf8_nao_perde_o_anexo():
    """Arquivo salvo no Bloco de Notas antigo/Windows vem em cp1252. Falhar aqui
    descartaria o anexo inteiro por causa de um "ç"."""
    saida = extract("nota.txt", "text/plain", "ação e coração".encode("cp1252"))

    assert saida == "ação e coração"


def test_desligar_texto_puro_e_respeitado():
    """O formato entra na mesma config por-modelo dos outros (Extração de texto →
    Formatos), então o toggle precisa valer para ele também."""
    with pytest.raises(ExtractionError):
        extract("nota.txt", "text/plain", b"qualquer coisa", {"text": False})


def test_teto_de_caracteres_corta_e_avisa():
    """Economia de tokens continua valendo para texto puro — mas truncar em silêncio
    faria o modelo responder sobre metade do arquivo achando que viu tudo."""
    saida = extract("grande.txt", "text/plain", b"x" * 5000, {"max_chars": 100})

    assert saida.startswith("x" * 100)
    assert "truncado" in saida


def test_formato_desconhecido_continua_recusado():
    """Nem tudo virou texto: um binário sem extensão conhecida tem de continuar fora
    da extração (o nome do arquivo ainda vai ao modelo)."""
    assert not is_extractable("programa.exe", "application/octet-stream")
    with pytest.raises(ExtractionError):
        extract("programa.exe", "application/octet-stream", b"MZ")


# --------------------------------------------------------------------------- #
# Caminho real: upload → texto guardado                                        #
# --------------------------------------------------------------------------- #
class _FakeDb:
    """Sessão mínima: o upload não precisa de Postgres para ser exercitado."""

    def __init__(self):
        self.commits = 0

    def add(self, row):
        return None

    async def commit(self):
        self.commits += 1

    async def refresh(self, row):
        return row

    async def scalar(self, _stmt):
        return 0


@pytest.mark.asyncio
async def test_txt_sobe_com_o_texto_ja_extraido(tmp_path, monkeypatch):
    """A extração acontece UMA vez, no upload, e o turno reusa. Se ela não rodar
    aqui, cada regeneração reabre o arquivo — ou, como acontecia, o anexo chega ao
    modelo como uma nota dizendo que não há texto."""
    import uuid

    from aiworkspace import uploads_service as svc
    from aiworkspace.config import get_settings
    from aiworkspace.uploads_routes import _extract_into

    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path), raising=False)
    db = _FakeDb()

    async def pedacos():
        yield "linha 1\nlinha 2\n".encode()

    row = await svc.store_stream(db, uuid.uuid4(), "Texto colado.txt", "text/plain", pedacos())
    assert row.kind == "file" and is_extractable(row.filename, row.mime)

    await _extract_into(db, row)

    assert row.text == "linha 1\nlinha 2\n"
