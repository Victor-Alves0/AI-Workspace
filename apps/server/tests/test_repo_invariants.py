"""Invariantes do repositório — as que ninguém percebe quebrar até doer.

Nenhum destes testes exercita comportamento: todos leem o repositório e cobram uma
regra que já custou caro (ou custaria) quando esquecida. É o mesmo espírito do
`test_version_sync`: a regra vira teste porque a memória humana não escala.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import aiworkspace
from aiworkspace.config import Settings

_SERVER = Path(aiworkspace.__file__).resolve().parent.parent   # apps/server
_REPO = _SERVER.parent.parent                                   # raiz do repositório
_VERSIONS = _SERVER / "alembic" / "versions"
_WEB = _REPO / "apps" / "web"


def _revisions() -> dict[str, dict]:
    """{revision: {down, arquivo}} lido dos módulos de migração (sem importar nada)."""
    out: dict[str, dict] = {}
    for path in sorted(_VERSIONS.glob("[0-9]*.py")):
        text = path.read_text(encoding="utf-8")
        rev = re.search(r'(?m)^revision(?::\s*str)?\s*=\s*"([^"]+)"', text)
        down = re.search(r'(?m)^down_revision(?::[^=]+)?=\s*(?:"([^"]+)"|None)', text)
        assert rev, f"{path.name}: sem `revision`"
        assert down, f"{path.name}: sem `down_revision`"
        out[rev.group(1)] = {"down": down.group(1), "file": path.name}
    return out


# --------------------------------------------------------------------------- #
# Migrações                                                                    #
# --------------------------------------------------------------------------- #
def test_migracoes_formam_uma_corrente_unica():
    """`alembic upgrade head` roda sozinho no boot (entrypoint.sh). Duas cabeças ou um
    elo quebrado derrubam a subida do servidor INTEIRO — em produção, no deploy."""
    revs = _revisions()
    assert revs, "nenhuma migração encontrada"

    bases = [r for r, meta in revs.items() if meta["down"] is None]
    assert len(bases) == 1, f"deveria haver uma única migração inicial: {bases}"

    apontados = [meta["down"] for meta in revs.values() if meta["down"]]
    orfas = [d for d in apontados if d not in revs]
    assert not orfas, f"down_revision aponta p/ migração inexistente: {orfas}"

    duplicadas = [d for d in set(apontados) if apontados.count(d) > 1]
    assert not duplicadas, f"duas migrações com o mesmo pai (corrente bifurcada): {duplicadas}"

    cabecas = [r for r in revs if r not in apontados]
    assert len(cabecas) == 1, f"múltiplas cabeças — alembic recusa: {cabecas}"

    # a corrente percorre TODAS (sem ilha solta)
    andadas, atual = 0, cabecas[0]
    while atual is not None:
        andadas += 1
        atual = revs[atual]["down"]
    assert andadas == len(revs), f"{len(revs) - andadas} migração(ões) fora da corrente"


def test_ordem_alfabetica_dos_arquivos_bate_com_a_ordem_da_corrente():
    """Os arquivos são lidos por nome quando alguém abre a pasta: se o número do
    arquivo não acompanhar a posição na corrente, a leitura humana ("qual foi a
    última migração?") passa a mentir."""
    revs = _revisions()
    for rev, meta in revs.items():
        numero_arquivo = meta["file"].split("_", 1)[0]
        numero_revisao = rev.split("_", 1)[0]
        assert numero_arquivo == numero_revisao, (
            f"{meta['file']} declara revision={rev} (prefixos diferentes)"
        )

    por_numero = sorted(revs, key=lambda r: r.split("_", 1)[0])
    corrente: list[str] = []
    atual = next(r for r in revs if r not in {m["down"] for m in revs.values()})
    while atual is not None:
        corrente.append(atual)
        atual = revs[atual]["down"]
    assert list(reversed(corrente)) == por_numero, "a numeração não segue a corrente"


# --------------------------------------------------------------------------- #
# Modelos                                                                      #
# --------------------------------------------------------------------------- #
def test_todo_modelo_esta_exportado_em_models():
    """Um modelo fora do `models/__init__.py` não entra no metadata: o autogenerate do
    alembic não vê a tabela e os relacionamentos quebram só em tempo de execução."""
    import aiworkspace.models as models

    faltando: list[str] = []
    for path in sorted((_SERVER / "aiworkspace" / "models").glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            herda_base = any(isinstance(b, ast.Name) and b.id == "Base" for b in node.bases)
            if herda_base and not hasattr(models, node.name):
                faltando.append(f"{path.name}:{node.name}")
    assert not faltando, f"modelos não exportados em models/__init__.py: {faltando}"


# --------------------------------------------------------------------------- #
# Configuração                                                                 #
# --------------------------------------------------------------------------- #
def test_toda_variavel_de_ambiente_aparece_no_env_example():
    """Regra do projeto: variável nova SEMPRE entra no `.env.example` (ativa ou
    comentada com o default). Sem isso ela existe só na cabeça de quem a criou — e
    quem for instalar o app nunca descobre que ela existe."""
    exemplo = (_REPO / ".env.example").read_text(encoding="utf-8")
    # o arquivo documenta tanto `VAR=` (ativa) quanto `#VAR=` / `# VAR=` (com o default)
    declaradas = set(re.findall(r"(?m)^\s*#?\s*([A-Z][A-Z0-9_]*)\s*=", exemplo))
    faltando = sorted(
        nome.upper() for nome in Settings.model_fields
        if nome.upper() not in declaradas
    )
    assert not faltando, f"variáveis sem menção no .env.example: {faltando}"


# --------------------------------------------------------------------------- #
# Interface: convenções que o toque exige                                      #
# --------------------------------------------------------------------------- #
# Saídas válidas para um elemento que só aparece no hover:
#   .touch-reveal             → fica visível no toque
#   data-touch="long-press"   → alcançável segurando o item
#   data-touch="decorative"   → não é controle (horário, seta, sobreposição)
_ALTERNATIVAS = ('touch-reveal', 'data-touch="long-press"', 'data-touch="decorative"')


def _tsx() -> list[Path]:
    return [
        p for p in sorted(_WEB.rglob("*.tsx"))
        if "node_modules" not in p.parts and ".next" not in p.parts
    ]


def _tag_em_volta(texto: str, posicao: int) -> str:
    """Trecho da TAG que contém `posicao` — o marcador costuma estar em outra linha
    (`data-touch` num atributo acima do `className`), então olhar a linha não basta."""
    inicio = texto.rfind("<", 0, posicao)
    fim = texto.find(">", posicao)
    return texto[max(0, inicio):fim + 1 if fim != -1 else len(texto)]


def test_controle_revelado_por_hover_continua_alcancavel_no_toque():
    """Desde que `hover:` só vale com mouse (hoverOnlyWhenSupported), um elemento que
    só aparece no hover some no celular. Ou ele é alcançável (classe/toque longo), ou é
    declarado decorativo — a escolha tem de ser explícita, porque o sintoma do
    esquecimento é "o app não responde ao toque", não um erro."""
    problemas: list[str] = []
    for path in _tsx():
        texto = path.read_text(encoding="utf-8")
        for achado in re.finditer(r"group-hover:opacity-100", texto):
            tag = _tag_em_volta(texto, achado.start())
            if "opacity-0" not in tag:
                continue
            if any(saida in tag for saida in _ALTERNATIVAS):
                continue
            linha = texto[:achado.start()].count(chr(10)) + 1
            problemas.append(f"{path.relative_to(_REPO)}:{linha}")
    assert not problemas, (
        "revelado só no hover e sem saída no toque — marque com `touch-reveal`, "
        f'data-touch="long-press" ou data-touch="decorative": {problemas}'
    )


def test_data_touch_so_aceita_os_valores_da_convencao():
    """Sem isto, um `data-touch="ok"` inventado calaria o teste acima sem resolver nada."""
    validos = {"long-press", "decorative"}
    invalidos: list[str] = []
    for path in _tsx():
        for valor in re.findall(r'data-touch="([^"]*)"', path.read_text(encoding="utf-8")):
            if valor not in validos:
                invalidos.append(f"{path.relative_to(_REPO)}: {valor!r}")
    assert not invalidos, f"valores fora da convenção {sorted(validos)}: {invalidos}"


def test_o_tailwind_mantem_hover_apenas_onde_ha_mouse():
    """A flag é a correção do 'precisa tocar duas vezes' no iOS. Se alguém a remover,
    todos os `touch-reveal` continuam certos e o bug volta silenciosamente."""
    config = (_WEB / "tailwind.config.ts").read_text(encoding="utf-8")
    assert "hoverOnlyWhenSupported: true" in config
