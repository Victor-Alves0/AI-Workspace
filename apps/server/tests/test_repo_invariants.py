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


def test_id_da_migracao_cabe_na_tabela_do_alembic():
    """`alembic_version.version_num` é VARCHAR(32). Um id maior passa em todo teste
    local (nenhum grava a versão) e só explode no deploy, no UPDATE final do upgrade:
    o servidor entra em loop de reinício. Aconteceu com `0078_chat_mini_app_and_setup_stage`
    (34 caracteres)."""
    longos = {rev: len(rev) for rev in _revisions() if len(rev) > 32}
    assert not longos, f"revision acima de 32 caracteres (VARCHAR(32) do alembic): {longos}"


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


def test_o_proxy_de_https_aponta_para_um_caddyfile_que_existe():
    """O HTTPS vem do serviço `proxy` montando um Caddyfile do repositório. Se o
    arquivo mudar de lugar, o container sobe com a config padrão do Caddy (uma página
    "Congratulations") e o app inteiro some do :443 — sem erro nenhum no `up`."""
    compose = (_REPO / "docker-compose.yml").read_text(encoding="utf-8")
    montagens = re.findall(r"- \./([^:\s]+):/etc/caddy(?:/Caddyfile)?:", compose)
    assert montagens, "serviço `proxy` sem a config do Caddy montada"
    for caminho in montagens:
        alvo = _REPO / caminho
        arquivo = alvo / "Caddyfile" if alvo.is_dir() else alvo
        assert arquivo.is_file(), f"Caddyfile ausente: {caminho}"
        texto = arquivo.read_text(encoding="utf-8")
        # as duas rotas que o front espera: /api → server, resto → web
        assert "handle_path /api/*" in texto and "reverse_proxy server:8000" in texto
        assert "reverse_proxy web:3000" in texto
        # cada modo de TLS precisa do arquivo que o Caddyfile importa: sem o
        # `on_demand` do modo local, o site curinga `:443` derruba o handshake
        # ("tlsv1 alert internal error") por não saber para qual nome emitir
        for modo, exigido in (("local", "on_demand"), ("public", "")):
            parte = arquivo.parent / f"tls-{modo}.caddy"
            assert parte.is_file(), f"faltando {parte.name} (importado pelo Caddyfile)"
            assert exigido in parte.read_text(encoding="utf-8")


def test_todo_volume_do_server_existe_e_pertence_ao_app_na_imagem():
    """O container roda como `app` (não-root). Um volume nomeado montado sobre um
    caminho que NÃO existe na imagem nasce pertencendo ao root — e o processo não
    consegue escrever nele. Foi assim que os anexos quebraram: `/data/uploads` estava
    no compose mas faltava no `mkdir`/`chown` do Dockerfile, e todo upload virava erro
    interno (que o navegador ainda mostrava como "erro de CORS")."""
    compose = (_REPO / "docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = (_SERVER / "Dockerfile").read_text(encoding="utf-8")
    # bloco do serviço `server` (até o próximo serviço no mesmo nível)
    bloco = re.search(r"(?ms)^  server:\n(.*?)(?=^  [a-z0-9_-]+:\n)", compose)
    assert bloco, "serviço `server` não encontrado no docker-compose.yml"
    # só volumes NOMEADOS (`nome:/caminho`); bind mounts do host (./x:/y) não contam
    montagens = re.findall(r"- ([a-z0-9_]+):(/[^\s:]+)", bloco.group(1))
    assert montagens, "nenhum volume nomeado no serviço `server` (regex desatualizada?)"

    criadas = " ".join(re.findall(r"mkdir -p ([^\\\n]+)", dockerfile)).split()
    chowned = " ".join(re.findall(r"chown -R app:app ([^\\\n]+)", dockerfile)).split()

    def coberto(destino: str, caminhos: list[str]) -> bool:
        # a própria pasta, ou um ancestral (chown -R /home/app cobre /home/app/.cache)
        return any(destino == c or destino.startswith(c.rstrip("/") + "/") for c in caminhos)

    faltando = [
        destino for _, destino in montagens
        if not coberto(destino, criadas) or not coberto(destino, chowned)
    ]
    assert not faltando, (
        f"volume(s) sem `mkdir -p`/`chown app:app` no Dockerfile do server: {faltando}"
    )


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
def test_toda_variavel_de_ambiente_esta_documentada():
    """Regra do projeto: variável nova SEMPRE aparece documentada. Sem isso ela
    existe só na cabeça de quem a criou — e quem instalar o app nunca descobre.

    São DOIS lugares, de propósito: o `.env.example` é curto (só o que exige
    decisão humana) e `docs/configuration.md` é a referência completa, com o
    default de cada uma. O que não couber no primeiro tem que estar no segundo."""
    exemplo = (_REPO / ".env.example").read_text(encoding="utf-8")
    referencia = (_REPO / "docs" / "configuration.md").read_text(encoding="utf-8")
    # `VAR=` (atribuição, nos dois) e `` `VAR` `` (tabelas da referência)
    texto = exemplo + referencia
    declaradas = set(re.findall(r"(?m)^\s*#?\s*([A-Z][A-Z0-9_]*)\s*=", texto))
    declaradas |= set(re.findall(r"`([A-Z][A-Z0-9_]{2,})`", referencia))
    faltando = sorted(
        nome.upper() for nome in Settings.model_fields
        if nome.upper() not in declaradas
    )
    assert not faltando, f"variáveis sem documentação: {faltando}"


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
