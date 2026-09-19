"""Lint das migrações — padrões que destroem dados ou derrubam o boot de quem atualiza.

Roda sem banco: lê o código das migrações. A bateria com Postgres de verdade fica em
`tests/db/` (e só roda com TEST_DATABASE_URL); este arquivo pega o erro antes, no
`pytest` de sempre, com a mensagem apontando o arquivo e a linha.

Toda migração roda sozinha no boot (`alembic upgrade head` no entrypoint), sobre o
banco de quem atualizou — ninguém revisa o SQL na hora. O que passa daqui chega lá.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import aiworkspace

_VERSIONS = Path(aiworkspace.__file__).resolve().parent.parent / "alembic" / "versions"

# Uma migração que APAGA dados de propósito declara isso no próprio arquivo, com o
# motivo. Sem o marcador, drop/delete/troca de tipo é tratado como acidente.
MARCADOR_DESTRUTIVO = "# destrutivo-aprovado:"

_SQL_DESTRUTIVO = re.compile(
    r"\b(DELETE\s+FROM|TRUNCATE|DROP\s+TABLE|DROP\s+COLUMN|DROP\s+SCHEMA|ALTER\s+COLUMN\s+\w+\s+TYPE)\b",
    re.IGNORECASE,
)
_OPS_DESTRUTIVAS = {"drop_table", "drop_column"}
_PG_IDENTIFICADOR_MAX = 63   # NAMEDATALEN - 1: o Postgres TRUNCA nomes maiores em silêncio


def _migracoes() -> list[tuple[Path, str, ast.Module]]:
    out = []
    for path in sorted(_VERSIONS.glob("[0-9]*.py")):
        texto = path.read_text(encoding="utf-8")
        out.append((path, texto, ast.parse(texto)))
    return out


def _funcao(tree: ast.Module, nome: str) -> ast.FunctionDef | None:
    return next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == nome), None
    )


def _chamadas_op(fn: ast.FunctionDef):
    """(nome, nó) de cada `op.<nome>(...)` / `batch_op.<nome>(...)` dentro da função."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            yield node.func.attr, node


def _texto_constante(node: ast.AST) -> str | None:
    """Conteúdo literal de `"..."`, `sa.text("...")` ou f-string sem interpolação."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call) and node.args:
        return _texto_constante(node.args[0])
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
        )
    return None


def _kw(node: ast.Call, nome: str) -> ast.AST | None:
    return next((k.value for k in node.keywords if k.arg == nome), None)


def _eh_false(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


# --------------------------------------------------------------------------- #
def test_nenhuma_migracao_importa_o_codigo_do_app():
    """Migração importando `aiworkspace.models` usa o modelo de HOJE para descrever o
    banco de ONTEM: quando o modelo mudar, a migração antiga quebra — e quem atualiza de
    uma versão velha fica com o boot travado no meio da corrente."""
    culpadas = [
        path.name for path, texto, _ in _migracoes()
        if re.search(r"(?m)^\s*(from|import)\s+aiworkspace\b", texto)
    ]
    assert not culpadas, f"migrações importando o app (congele o SQL/colunas nelas): {culpadas}"


def test_toda_migracao_define_upgrade_e_downgrade():
    faltando = [
        f"{path.name}: {nome}()"
        for path, _, tree in _migracoes()
        for nome in ("upgrade", "downgrade")
        if _funcao(tree, nome) is None
    ]
    assert not faltando, f"sem função obrigatória: {faltando}"


def test_coluna_not_null_nova_em_tabela_existente_tem_server_default():
    """`add_column(..., nullable=False)` sem `server_default` passa no banco vazio do
    desenvolvedor e FALHA no banco de quem já tem linhas ("column contains null
    values") — o upgrade aborta e o servidor não sobe. Ou dê um server_default, ou
    adicione nullable, preencha com UPDATE e só então aperte o NOT NULL."""
    problemas = []
    for path, _, tree in _migracoes():
        up = _funcao(tree, "upgrade")
        if up is None:
            continue
        criadas_aqui = {
            _texto_constante(n.args[0]) for nome, n in _chamadas_op(up)
            if nome == "create_table" and n.args
        }
        for nome, n in _chamadas_op(up):
            if nome != "add_column" or len(n.args) < 2 or not isinstance(n.args[1], ast.Call):
                continue
            tabela = _texto_constante(n.args[0])
            if tabela in criadas_aqui:
                continue
            coluna = n.args[1]
            if not _eh_false(_kw(coluna, "nullable")):
                continue
            if _kw(coluna, "server_default") is not None:
                continue
            # padrão seguro em 3 passos: add nullable=True → UPDATE → alter nullable=False
            # (esse caso nem chega aqui, pois o add é nullable). Aqui só o add direto.
            nome_col = _texto_constante(coluna.args[0]) if coluna.args else "?"
            problemas.append(f"{path.name}:{n.lineno} {tabela}.{nome_col}")
    assert not problemas, f"NOT NULL sem server_default em tabela existente: {problemas}"


def test_alter_para_not_null_vem_depois_de_preencher_os_nulos():
    """`alter_column(nullable=False)` numa coluna que já existia falha se houver algum
    NULL. Exige um UPDATE/`server_default` na MESMA migração antes do aperto."""
    problemas = []
    for path, texto, tree in _migracoes():
        up = _funcao(tree, "upgrade")
        if up is None:
            continue
        criadas_aqui = {
            _texto_constante(n.args[0]) for nome, n in _chamadas_op(up)
            if nome == "create_table" and n.args
        }
        for nome, n in _chamadas_op(up):
            if nome != "alter_column" or not _eh_false(_kw(n, "nullable")):
                continue
            tabela = _texto_constante(n.args[0]) if n.args else None
            if tabela in criadas_aqui:
                continue
            corpo = ast.get_source_segment(texto, up) or ""
            antes = corpo[: corpo.find(ast.get_source_segment(texto, n) or "")]
            if re.search(r"\bUPDATE\b", antes, re.IGNORECASE) or _kw(n, "server_default") is not None:
                continue
            problemas.append(f"{path.name}:{n.lineno} {tabela}")
    assert not problemas, f"NOT NULL apertado sem preencher os nulos antes: {problemas}"


def test_operacao_destrutiva_exige_marcador_explicito():
    """drop_table / drop_column / DELETE / TRUNCATE / troca de tipo dentro do upgrade()
    apagam (ou podem truncar) dado de usuário SEM volta — o downgrade não traz de volta
    o que foi dropado. Pode ser a intenção; então diga, no arquivo:
        # destrutivo-aprovado: <por que é seguro apagar isto>"""
    problemas = []
    for path, texto, tree in _migracoes():
        if MARCADOR_DESTRUTIVO in texto:
            continue
        up = _funcao(tree, "upgrade")
        if up is None:
            continue
        for nome, n in _chamadas_op(up):
            if nome in _OPS_DESTRUTIVAS:
                problemas.append(f"{path.name}:{n.lineno} op.{nome}")
            elif nome == "alter_column" and _kw(n, "type_") is not None:
                problemas.append(f"{path.name}:{n.lineno} alter_column(type_=...)")
            elif nome == "alter_column" and _kw(n, "new_column_name") is not None:
                # renomear quebra o código ANTIGO que ainda roda até o restart
                problemas.append(f"{path.name}:{n.lineno} alter_column(new_column_name=...)")
            elif nome == "execute" and n.args:
                sql = _texto_constante(n.args[0]) or ""
                if _SQL_DESTRUTIVO.search(sql):
                    problemas.append(f"{path.name}:{n.lineno} execute({sql.strip()[:60]!r})")
    assert not problemas, (
        f"operação destrutiva sem `{MARCADOR_DESTRUTIVO} <motivo>` no arquivo: {problemas}"
    )


def test_marcador_destrutivo_traz_um_motivo():
    """Sem isto, um marcador vazio calaria o teste acima sem registrar nada."""
    vazios = [
        path.name for path, texto, _ in _migracoes()
        for linha in texto.splitlines()
        if linha.strip().startswith(MARCADOR_DESTRUTIVO)
        and len(linha.strip()[len(MARCADOR_DESTRUTIVO):].strip()) < 10
    ]
    assert not vazios, f"marcador destrutivo sem motivo: {vazios}"


def test_nomes_de_indice_e_constraint_cabem_no_postgres():
    """O Postgres corta identificadores acima de 63 bytes SEM erro: dois índices com o
    mesmo prefixo longo colidem, e um `drop_index` futuro pelo nome inteiro não acha o
    objeto — a migração seguinte quebra no banco de quem tem aquele índice."""
    longos = []
    for path, _, tree in _migracoes():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            candidatos = []
            if node.func.attr in {"create_index", "create_unique_constraint",
                                  "create_foreign_key", "create_check_constraint",
                                  "create_table", "add_column"} and node.args:
                candidatos.append(node.args[0])
            nome_kw = _kw(node, "name")
            if nome_kw is not None:
                candidatos.append(nome_kw)
            for c in candidatos:
                valor = _texto_constante(c)
                if valor and len(valor.encode("utf-8")) > _PG_IDENTIFICADOR_MAX:
                    longos.append(f"{path.name}:{node.lineno} {valor} ({len(valor)})")
    assert not longos, f"identificador acima de 63 bytes (truncado pelo Postgres): {longos}"


def test_extensao_criada_de_forma_idempotente():
    """`CREATE EXTENSION vector` sem IF NOT EXISTS falha em banco restaurado de backup
    (a extensão já veio no dump) — o boot fica em loop."""
    problemas = []
    for path, texto, _ in _migracoes():
        for m in re.finditer(r"CREATE\s+EXTENSION\s+(?!IF\s+NOT\s+EXISTS)", texto, re.IGNORECASE):
            problemas.append(f"{path.name}:{texto[:m.start()].count(chr(10)) + 1}")
    assert not problemas, f"CREATE EXTENSION sem IF NOT EXISTS: {problemas}"


def test_create_index_concurrently_nao_roda_dentro_da_transacao():
    """O env.py roda a corrente inteira numa transação (se algo falha, nada fica pela
    metade). `CREATE INDEX CONCURRENTLY` é proibido dentro de transação: o upgrade
    inteiro abortaria no boot."""
    culpadas = [
        path.name for path, texto, _ in _migracoes()
        if re.search(r"CONCURRENTLY", texto, re.IGNORECASE)
        or re.search(r"postgresql_concurrently\s*=\s*True", texto)
    ]
    assert not culpadas, f"índice CONCURRENTLY dentro da transação das migrações: {culpadas}"
