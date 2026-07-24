"""Codespace: path-jail + escopo allow/deny (o vetor de segurança das tools
code.files.browse), truncamento de assinatura e o header de autenticação do
clone git. Puro/hermético — sem DB, sem CodeGraph real (monkeypatcha _DATA_ROOT
com um diretório de arquivos reais em tmp_path)."""
from __future__ import annotations

import base64
import json

import pytest

from aiworkspace.codespace import graph_service as gs


# --------------------------------------------------------------------------- #
# safe_path — jail
# --------------------------------------------------------------------------- #
def test_safe_path_allows_inside_root(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x")
    assert gs.safe_path(tmp_path, "src/a.py") == (tmp_path / "src" / "a.py").resolve()


def test_safe_path_root_itself():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        from pathlib import Path
        root = Path(d)
        assert gs.safe_path(root, "") == root.resolve()
        assert gs.safe_path(root, ".") == root.resolve()


def test_safe_path_blocks_parent_escape(tmp_path):
    with pytest.raises(ValueError):
        gs.safe_path(tmp_path, "../outside.txt")


def test_safe_path_blocks_absolute_style_escape(tmp_path):
    # mesmo um path "absoluto" some — join com root sempre, e é revalidado
    # contra o pai; um symlink apontando pra fora também cai na checagem de parents
    with pytest.raises(ValueError):
        gs.safe_path(tmp_path, "../../etc/passwd")


def test_safe_path_blocks_symlink_escape(tmp_path):
    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("segredo")
    link = tmp_path / "escape"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks não suportados neste ambiente")
    with pytest.raises(ValueError):
        gs.safe_path(tmp_path, "escape/secret.txt")


# --------------------------------------------------------------------------- #
# is_allowed — escopo (allow/deny globs)
# --------------------------------------------------------------------------- #
def test_is_allowed_default_denies_git_dir(tmp_path):
    p = tmp_path / ".git" / "config"
    p.parent.mkdir()
    p.touch()
    assert gs.is_allowed(tmp_path, p, {}) is False


def test_is_allowed_deny_glob(tmp_path):
    p = tmp_path / "secrets" / "keys.env"
    p.parent.mkdir()
    p.touch()
    assert gs.is_allowed(tmp_path, p, {"deny": ["secrets/**"]}) is False


def test_is_allowed_allow_glob_restricts(tmp_path):
    src = tmp_path / "src" / "a.py"
    src.parent.mkdir()
    src.touch()
    other = tmp_path / "docs" / "readme.md"
    other.parent.mkdir()
    other.touch()
    scope = {"allow": ["src/**"]}
    assert gs.is_allowed(tmp_path, src, scope) is True
    assert gs.is_allowed(tmp_path, other, scope) is False


def test_is_allowed_no_scope_allows_everything_but_defaults(tmp_path):
    p = tmp_path / "app.py"
    p.touch()
    assert gs.is_allowed(tmp_path, p, None) is True
    assert gs.is_allowed(tmp_path, p, {}) is True


# --------------------------------------------------------------------------- #
# _short — assinatura truncada, campos compactos
# --------------------------------------------------------------------------- #
def test_short_truncates_long_signature():
    sym = {"fqn": "a.b.c", "kind": "function", "path": "a.py", "start_line": 1,
           "end_line": 5, "signature": "x" * 2000, "doc": "y" * 500}
    out = gs._short(sym)
    assert out["fqn"] == "a.b.c"
    assert len(out["signature"]) <= gs._MAX_SIG + 1
    assert out["signature"].endswith("…")
    assert len(out["doc"]) <= gs._MAX_DOC


def test_short_none_passthrough():
    assert gs._short(None) is None
    assert gs._short({}) is not None


# --------------------------------------------------------------------------- #
# _auth_header — Basic auth para git clone (NÃO vai na URL/config do repo)
# --------------------------------------------------------------------------- #
def test_auth_header_format():
    h = gs._auth_header("mytoken123")
    assert h.startswith("Authorization: Basic ")
    b64 = h.split("Authorization: Basic ")[1]
    decoded = base64.b64decode(b64).decode()
    assert decoded == "x-access-token:mytoken123"


# --------------------------------------------------------------------------- #
# list_files / read_file / search_files — contra arquivos REAIS em tmp_path
# --------------------------------------------------------------------------- #
@pytest.fixture
def fake_project(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "_DATA_ROOT", tmp_path)
    user_id, project_id = "u1", "p1"
    root = gs.working_copy_path(user_id, project_id)
    root.mkdir(parents=True)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("\n".join(f"line {i}" for i in range(1, 11)) + "\nTODO fix this\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("secret stuff")
    return user_id, project_id, root


def test_list_files_excludes_git_by_default(fake_project):
    user_id, project_id, root = fake_project
    out = gs.list_files(user_id, project_id, {}, path="", max_depth=3)
    paths = [e["path"] for e in out["entries"]]
    assert "src" in paths
    assert not any(".git" in p for p in paths)


def test_list_files_missing_project_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "_DATA_ROOT", tmp_path)
    out = gs.list_files("nouser", "noproj", {}, path="")
    assert "error" in out


def test_read_file_returns_numbered_lines(fake_project):
    user_id, project_id, _root = fake_project
    out = gs.read_file(user_id, project_id, {}, "src/app.py")
    assert out["total_lines"] == 11
    assert out["content"].splitlines()[0].startswith("1\t")


def test_read_file_respects_deny_scope(fake_project):
    user_id, project_id, _root = fake_project
    out = gs.read_file(user_id, project_id, {"deny": ["src/**"]}, "src/app.py")
    assert "error" in out


def test_read_file_rejects_path_escape(fake_project):
    user_id, project_id, _root = fake_project
    with pytest.raises(ValueError):
        gs.read_file(user_id, project_id, {}, "../../etc/passwd")


def test_search_files_finds_text(fake_project):
    user_id, project_id, _root = fake_project
    out = gs.search_files(user_id, project_id, {}, "TODO")
    assert out["results"]
    assert out["results"][0]["path"] == "src/app.py"


def test_search_files_respects_glob(fake_project):
    user_id, project_id, root = fake_project
    (root / "src" / "notes.md").write_text("TODO in markdown")
    out = gs.search_files(user_id, project_id, {}, "TODO", glob="*.py")
    paths = {r["path"] for r in out["results"]}
    assert "src/app.py" in paths
    assert "src/notes.md" not in paths


# --------------------------------------------------------------------------- #
# search_files via ripgrep — o "grep" da IA. Regex, arquivos SEM extensão
# conhecida (Dockerfile…), contexto, files_only e case-sensitivity.
# --------------------------------------------------------------------------- #
def test_search_finds_extensionless_files(fake_project):
    """O caminho antigo só olhava uma lista fixa de extensões: um Dockerfile
    (sem extensão) era INVISÍVEL pra IA."""
    _uid, _pid, root = fake_project
    (root / "Dockerfile").write_text("FROM python:3.12-slim\nRUN apt-get install ripgrep\n")
    out = gs.search_files(_uid, _pid, {}, "ripgrep")
    assert any(r["path"] == "Dockerfile" for r in out["results"])


def test_search_regex_mode(fake_project):
    user_id, project_id, root = fake_project
    (root / "src" / "h.py").write_text("def handle_a():\n    pass\ndef handle_b():\n    pass\n")
    out = gs.search_files(user_id, project_id, {}, r"def handle_[ab]\(", regex=True)
    assert len(out["results"]) == 2


def test_search_literal_by_default_does_not_interpret_regex(fake_project):
    """Sem regex=True, `.` e `(` são texto — não metacaracteres."""
    user_id, project_id, root = fake_project
    (root / "src" / "lit.py").write_text("a.b(c)\naXbYcZ\n")
    out = gs.search_files(user_id, project_id, {}, "a.b(c)")
    assert len(out["results"]) == 1
    assert "a.b(c)" in out["results"][0]["text"]


def test_search_case_sensitivity(fake_project):
    user_id, project_id, root = fake_project
    (root / "src" / "case.py").write_text("Config = 1\nconfig = 2\n")
    assert len(gs.search_files(user_id, project_id, {}, "config")["results"]) == 2
    strict = gs.search_files(user_id, project_id, {}, "config", case_sensitive=True)
    assert len(strict["results"]) == 1


def test_search_files_only_returns_paths(fake_project):
    user_id, project_id, root = fake_project
    (root / "src" / "many.py").write_text("x\n" * 3 + "needle\nneedle\nneedle\n")
    out = gs.search_files(user_id, project_id, {}, "needle", files_only=True)
    assert out["files"] == ["src/many.py"]   # 1 entrada, não 3 linhas
    assert "results" not in out


def test_search_context_lines(fake_project):
    user_id, project_id, root = fake_project
    (root / "src" / "ctx.py").write_text("linha1\nlinha2\nALVO\nlinha4\nlinha5\n")
    out = gs.search_files(user_id, project_id, {}, "ALVO", context=1)
    texts = [r["text"] for r in out["results"]]
    assert "linha2" in texts and "ALVO" in texts and "linha4" in texts


def test_search_invalid_regex_reports_error(fake_project):
    user_id, project_id, _root = fake_project
    out = gs.search_files(user_id, project_id, {}, "def (", regex=True)
    assert "error" in out


def test_search_query_starting_with_dash_is_not_a_flag(fake_project):
    """Sem o `--` antes do padrão, um query tipo '--files' vira FLAG do rg
    (mesma classe de injeção de argumento já corrigida no git_diff)."""
    user_id, project_id, root = fake_project
    (root / "src" / "dash.py").write_text("valor = --files\n")
    out = gs.search_files(user_id, project_id, {}, "--files")
    assert "error" not in out
    assert any(r["path"] == "src/dash.py" for r in out["results"])


def test_search_still_respects_deny_scope(fake_project):
    user_id, project_id, root = fake_project
    (root / "secrets").mkdir()
    (root / "secrets" / "keys.env").write_text("TOKEN=segredo\n")
    out = gs.search_files(user_id, project_id, {"deny": ["secrets/**"]}, "segredo")
    assert out.get("results") == []


def test_search_never_reads_the_git_dir(fake_project):
    user_id, project_id, _root = fake_project
    # o fixture grava "secret stuff" em .git/config
    out = gs.search_files(user_id, project_id, {}, "secret stuff")
    assert out.get("results") == []


# --------------------------------------------------------------------------- #
# find_files — busca por NOME (barra de busca do explorador), diferente de
# search_files (conteúdo)
# --------------------------------------------------------------------------- #
def test_find_files_matches_by_name_substring(fake_project):
    user_id, project_id, _root = fake_project
    out = gs.find_files(user_id, project_id, {}, "app")
    paths = [e["path"] for e in out["entries"]]
    assert "src/app.py" in paths


def test_find_files_excludes_git_by_default(fake_project):
    user_id, project_id, _root = fake_project
    out = gs.find_files(user_id, project_id, {}, "config")
    assert out["entries"] == []  # .git/config nunca aparece


def test_find_files_empty_query_returns_nothing(fake_project):
    user_id, project_id, _root = fake_project
    out = gs.find_files(user_id, project_id, {}, "")
    assert out["entries"] == []


def test_find_files_respects_deny_scope(fake_project):
    user_id, project_id, _root = fake_project
    out = gs.find_files(user_id, project_id, {"deny": ["src/**"]}, "app")
    assert out["entries"] == []


# --------------------------------------------------------------------------- #
# Escrita + git — write/edit/delete AUTO-COMMITAM; log/diff só leem. Repo git
# REAL (git init em tmp_path), sem rede/remoto — hermético.
# --------------------------------------------------------------------------- #
import subprocess  # noqa: E402


@pytest.fixture
def fake_git_project(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "_DATA_ROOT", tmp_path)
    user_id, project_id = "u1", "p1"
    root = gs.working_copy_path(user_id, project_id)
    root.mkdir(parents=True)
    (root / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t.com", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t.com", "commit", "-q", "-m", "initial"],
                    cwd=root, check=True)
    yield user_id, project_id, root
    gs.invalidate(project_id)


def test_write_file_creates_and_commits(fake_git_project):
    user_id, project_id, root = fake_git_project
    out = gs.write_file(user_id, project_id, {}, "new/thing.py", "x = 1\n", message="add thing")
    assert out["ok"] is True
    assert out["created"] is True
    assert out["commit"]["message"] == "add thing"
    assert (root / "new" / "thing.py").read_text() == "x = 1\n"
    log = subprocess.run(["git", "log", "--oneline"], cwd=root, capture_output=True, text=True)
    assert "add thing" in log.stdout


def test_write_file_overwrite_not_created(fake_git_project):
    user_id, project_id, _root = fake_git_project
    out = gs.write_file(user_id, project_id, {}, "app.py", "print('bye')\n")
    assert out["created"] is False
    assert out["commit"] is not None


def test_write_file_same_content_no_commit(fake_git_project):
    user_id, project_id, _root = fake_git_project
    out = gs.write_file(user_id, project_id, {}, "app.py", "print('hello')\n")
    assert out["commit"] is None  # nothing to commit — working tree já limpa


def test_write_file_rejects_scope(fake_git_project):
    user_id, project_id, _root = fake_git_project
    out = gs.write_file(user_id, project_id, {"deny": ["secrets/**"]}, "secrets/key.txt", "x")
    assert "error" in out


def test_write_file_rejects_path_escape(fake_git_project):
    user_id, project_id, _root = fake_git_project
    with pytest.raises(ValueError):
        gs.write_file(user_id, project_id, {}, "../../etc/passwd", "x")


def test_write_file_size_cap(fake_git_project):
    user_id, project_id, _root = fake_git_project
    out = gs.write_file(user_id, project_id, {}, "big.txt", "x" * (gs._MAX_FILE_BYTES + 1))
    assert "error" in out


def test_edit_file_unique_match_commits(fake_git_project):
    user_id, project_id, root = fake_git_project
    out = gs.edit_file(user_id, project_id, {}, "app.py", "hello", "world", message="rename")
    assert out["ok"] is True
    assert (root / "app.py").read_text() == "print('world')\n"
    assert out["commit"]["message"] == "rename"


def test_edit_file_not_found(fake_git_project):
    user_id, project_id, _root = fake_git_project
    out = gs.edit_file(user_id, project_id, {}, "app.py", "does-not-exist", "x")
    assert "error" in out


def test_edit_file_ambiguous_match(fake_git_project):
    user_id, project_id, root = fake_git_project
    (root / "app.py").write_text("a\na\n")
    out = gs.edit_file(user_id, project_id, {}, "app.py", "a", "b")
    assert "error" in out
    assert "2" in out["error"]


def test_edit_file_missing_file(fake_git_project):
    user_id, project_id, _root = fake_git_project
    out = gs.edit_file(user_id, project_id, {}, "nope.py", "x", "y")
    assert "error" in out


def test_delete_file_commits(fake_git_project):
    user_id, project_id, root = fake_git_project
    out = gs.delete_file(user_id, project_id, {}, "app.py", message="remove app")
    assert out["ok"] is True
    assert not (root / "app.py").exists()
    assert out["commit"]["message"] == "remove app"


def test_delete_file_missing(fake_git_project):
    user_id, project_id, _root = fake_git_project
    out = gs.delete_file(user_id, project_id, {}, "nope.py")
    assert "error" in out


# --------------------------------------------------------------------------- #
# move_file — arraste-e-solte/renomear no explorador humano (auto-commita, como
# write/edit/delete)
# --------------------------------------------------------------------------- #
def test_move_file_renames_and_commits(fake_git_project):
    user_id, project_id, root = fake_git_project
    out = gs.move_file(user_id, project_id, {}, "app.py", "main.py", message="rename app")
    assert out["ok"] is True
    assert out["path"] == "main.py"
    assert not (root / "app.py").exists()
    assert (root / "main.py").exists()
    assert out["commit"]["message"] == "rename app"


def test_move_file_into_folder_creates_parent(fake_git_project):
    user_id, project_id, root = fake_git_project
    out = gs.move_file(user_id, project_id, {}, "app.py", "src/app.py")
    assert out["ok"] is True
    assert (root / "src" / "app.py").exists()


def test_move_file_missing_source_errors(fake_git_project):
    user_id, project_id, _root = fake_git_project
    out = gs.move_file(user_id, project_id, {}, "nope.py", "elsewhere.py")
    assert "error" in out


def test_move_file_dest_already_exists_errors(fake_git_project):
    user_id, project_id, root = fake_git_project
    (root / "other.py").write_text("x = 1\n")
    out = gs.move_file(user_id, project_id, {}, "app.py", "other.py")
    assert "error" in out


def test_move_file_rejects_scope(fake_git_project):
    user_id, project_id, _root = fake_git_project
    out = gs.move_file(user_id, project_id, {"deny": ["secrets/**"]}, "app.py", "secrets/app.py")
    assert "error" in out


def test_move_file_rejects_folder_into_itself(fake_git_project):
    user_id, project_id, root = fake_git_project
    (root / "pkg").mkdir()
    (root / "pkg" / "mod.py").write_text("x = 1\n")
    out = gs.move_file(user_id, project_id, {}, "pkg", "pkg/nested")
    assert "error" in out


def test_git_log_returns_commits(fake_git_project):
    user_id, project_id, _root = fake_git_project
    gs.write_file(user_id, project_id, {}, "b.py", "y = 2\n", message="second")
    out = gs.git_log(user_id, project_id, limit=10)
    messages = [c["message"] for c in out["commits"]]
    assert "second" in messages
    assert "initial" in messages


def test_git_log_missing_project(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "_DATA_ROOT", tmp_path)
    out = gs.git_log("nouser", "noproj")
    assert "error" in out


def test_git_diff_uncommitted_empty_by_default(fake_git_project):
    user_id, project_id, _root = fake_git_project
    out = gs.git_diff(user_id, project_id, {})
    assert out["diff"] == "(sem mudanças)"  # cada write já commita — nada solto


def test_git_diff_shows_last_commit(fake_git_project):
    user_id, project_id, _root = fake_git_project
    gs.write_file(user_id, project_id, {}, "app.py", "print('changed')\n")
    out = gs.git_diff(user_id, project_id, {}, ref="HEAD~1")
    assert "changed" in out["diff"]


def test_git_diff_rejects_scope(fake_git_project):
    user_id, project_id, _root = fake_git_project
    out = gs.git_diff(user_id, project_id, {"deny": ["app.py"]}, path="app.py")
    assert "error" in out


def test_git_diff_rejects_flag_like_ref(fake_git_project, tmp_path):
    """`ref` vira um argumento solto pro `git diff` — sem essa checagem,
    "--output=<arquivo>" é lido como FLAG (não revisão) e faz o git ESCREVER
    o diff onde o atacante quiser em vez de devolvê-lo (injeção de argumento)."""
    user_id, project_id, _root = fake_git_project
    target = tmp_path / "pwned.txt"
    out = gs.git_diff(user_id, project_id, {}, ref=f"--output={target}")
    assert "error" in out
    assert not target.exists()


# --------------------------------------------------------------------------- #
# Consultas avançadas do grafo — contra um índice CodeGraph REAL (projeto Python
# minúsculo em tmp_path). Sem rede, sem DB: só a lib + arquivos de verdade.
# --------------------------------------------------------------------------- #
_APP_PY = '''
from db import save_user

def get_env(name):
    import os
    return os.environ.get(name)

def handle(req):
    """Entrada HTTP."""
    user = req["user"]
    save_user(user)
    return render(user)

def render(user):
    return f"<b>{user}</b>"

def main():
    handle({"user": get_env("U")})
'''

_DB_PY = '''
_S = {}

def save_user(user):
    _S[user] = 1
'''


@pytest.fixture
def indexed_project(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "_DATA_ROOT", tmp_path)
    user_id, project_id = "u2", "p2"
    root = gs.working_copy_path(user_id, project_id)
    root.mkdir(parents=True)
    (root / "app.py").write_text(_APP_PY)
    (root / "db.py").write_text(_DB_PY)
    gs._get_graph(user_id, project_id).index()
    yield user_id, project_id, root
    gs.invalidate(project_id)


def test_overview_lists_files_with_ranked_symbols(indexed_project):
    user_id, project_id, _root = indexed_project
    out = gs.overview(user_id, project_id, token_budget=800)
    paths = {f["path"] for f in out["files"]}
    assert "app.py" in paths
    assert out["total_files"] >= 2
    assert "warnings" in out
    # símbolos vêm compactos (sem body_hash/file_id crus da lib)
    sym = out["files"][0]["symbols"][0]
    assert set(sym) == {"fqn", "kind", "line", "signature"}


def test_references_finds_callers_and_imports(indexed_project):
    user_id, project_id, _root = indexed_project
    out = gs.references(user_id, project_id, "save_user")
    kinds = {r["kind"] for r in out["references"]}
    assert "calls" in kinds     # app.handle chama
    assert "imports" in kinds   # app.py importa
    assert out["total_found"] >= 2


def test_references_kind_filter(indexed_project):
    user_id, project_id, _root = indexed_project
    out = gs.references(user_id, project_id, "save_user", kind="imports")
    assert {r["kind"] for r in out["references"]} == {"imports"}


def test_callees_is_the_inverse_of_callers(indexed_project):
    user_id, project_id, _root = indexed_project
    out = gs.callees(user_id, project_id, "handle")
    alvos = {c["fqn"] for c in out["callees"]}
    assert any("save_user" in (a or "") for a in alvos)
    assert any("render" in (a or "") for a in alvos)


def test_callees_unresolved_edge_keeps_raw_name(indexed_project):
    """Aresta não resolvida tem other_fqn=None — devolver o nome cru é melhor
    que devolver null (o modelo ainda aprende o que foi chamado)."""
    user_id, project_id, _root = indexed_project
    out = gs.callees(user_id, project_id, "get_env", depth=2)
    assert all(c["fqn"] for c in out["callees"])


def test_symbol_info_has_counts(indexed_project):
    user_id, project_id, _root = indexed_project
    out = gs.symbol_info(user_id, project_id, "handle")
    assert out["symbol"]["fqn"].endswith("handle")
    assert out["counts"]["callees"] >= 1


def test_communities_groups_the_codebase(indexed_project):
    user_id, project_id, _root = indexed_project
    out = gs.communities(user_id, project_id, min_size=2)
    assert "communities" in out and "meta" in out


def test_doctor_reports_health_without_leaking_server_path(indexed_project):
    """`root` é o caminho absoluto no servidor — nunca vai pro modelo."""
    user_id, project_id, _root = indexed_project
    out = gs.doctor(user_id, project_id)
    assert out["files"] >= 2
    assert "root" not in out
    assert "/data/codespace" not in json.dumps(out)


def test_data_flow_traces_parameters(indexed_project):
    user_id, project_id, _root = indexed_project
    out = gs.data_flow(user_id, project_id, "handle")
    assert out["function"]["fqn"].endswith("handle")
    assert isinstance(out["params"], list)
    assert out["warnings"]  # o aviso de "may-taint/over-aproxima" NUNCA some


def test_reaches_returns_call_chains(indexed_project):
    user_id, project_id, _root = indexed_project
    out = gs.reaches(user_id, project_id, "main", sink="http")
    assert "paths" in out and "total_found" in out
    assert out["warnings"]


def test_taint_scan_shape(indexed_project):
    user_id, project_id, _root = indexed_project
    out = gs.taint(user_id, project_id)
    assert out["mode"] == "scan"
    assert "findings" in out and out["scanned"] >= 1
    assert out["warnings"]


def test_taint_entry_mode(indexed_project):
    user_id, project_id, _root = indexed_project
    out = gs.taint(user_id, project_id, entry="handle")
    assert out["mode"] == "entry"


def test_auth_header_never_in_git_config(fake_git_project):
    """O token do push NUNCA fica gravado em .git/config (usamos -c http.extraHeader,
    não a URL) — só confirmamos que o commit normal não grava nada parecido."""
    user_id, project_id, root = fake_git_project
    gs.write_file(user_id, project_id, {}, "x.py", "1")
    cfg = (root / ".git" / "config").read_text()
    assert "token" not in cfg.lower()
    assert "authorization" not in cfg.lower()


# --------------------------------------------------------------------------- #
# unsupported_extensions — o diagnóstico do "indexou e deu zero"
# --------------------------------------------------------------------------- #
def test_unsupported_extensions_lists_what_the_graph_cannot_read(tmp_path):
    """Projeto só de formatos sem gramática: o grafo indexa zero e o painel
    precisa dizer POR QUE, senão um reindex correto parece quebrado.
    (.html/.css saíram daqui no GraphCodeMap 45a35c4 — extractor dedicado.)"""
    (tmp_path / "notas.rtf").write_text("x")
    (tmp_path / "dados.parquet").write_text("x")
    assert set(gs.unsupported_extensions(tmp_path)) == {".rtf", ".parquet"}


def test_html_and_css_are_now_graph_supported(tmp_path):
    """Regressão do upgrade: front estático deixa de ser 'unsupported'."""
    (tmp_path / "index.html").write_text("<html></html>")
    (tmp_path / "style.css").write_text(".a{}")
    assert gs.unsupported_extensions(tmp_path) == []


def test_unsupported_extensions_ignores_supported_and_git(tmp_path):
    (tmp_path / "app.py").write_text("x = 1")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "COMMIT_EDITMSG").write_text("oi")
    (tmp_path / "notas.rtf").write_text("x")
    assert gs.unsupported_extensions(tmp_path) == [".rtf"]


def test_unsupported_extensions_ranks_by_frequency_and_caps(tmp_path):
    for i in range(3):
        (tmp_path / f"p{i}.rtf").write_text("x")
    (tmp_path / "só_um.parquet").write_text("x")
    assert gs.unsupported_extensions(tmp_path, limit=1) == [".rtf"]


# --------------------------------------------------------------------------- #
# O whitelist de `returns` da SIFT não pode engolir o diagnóstico
# --------------------------------------------------------------------------- #
def test_returns_whitelist_covers_status_and_doctor(indexed_project):
    """`returns` é um FILTRO: chave ausente some antes de chegar ao modelo.

    'status' e 'doctor' existem para explicar um índice vazio ou uma busca que
    não acha nada — se as chaves de diagnóstico caírem aqui, as duas ações
    respondem "files/symbols" e não explicam nada. Testar o graph_service
    isolado não pega isso; só a comparação com o whitelist real pega."""
    from aiworkspace.tools.sift_service import CODE_GRAPH_RETURNS

    user_id, project_id, _root = indexed_project
    allowed = set(CODE_GRAPH_RETURNS)
    for action, out in (("status", gs.status(user_id, project_id)),
                        ("doctor", gs.doctor(user_id, project_id))):
        faltando = set(out) - allowed
        assert not faltando, f"'{action}' perde as chaves {sorted(faltando)} no retorno"


def test_doctor_never_leaks_the_server_path(indexed_project):
    user_id, project_id, _root = indexed_project
    assert "root" not in gs.doctor(user_id, project_id)


# --------------------------------------------------------------------------- #
# API de host do GraphCodeMap 45a35c4: exclude= e symbol_changes
# --------------------------------------------------------------------------- #
def test_index_exclude_keeps_denied_symbols_out_of_the_graph(indexed_project):
    """O deny do escopo vira política de indexação: assinatura/docstring de um
    arquivo negado NUNCA entram no grafo (era o vazamento do find/references —
    o read recusava, mas o grafo respondia)."""
    user_id, project_id, root = indexed_project
    (root / "segredo.py").write_text(
        'def assina_token(payload):\n    """Assina com a chave mestra."""\n    return payload\n'
    )
    cg = gs._get_graph(user_id, project_id)
    cg.index(exclude=["segredo.py"])
    out = gs.find(user_id, project_id, "assina")
    assert out["symbols"] == []
    # e a política PERSISTE: um reindex incremental sem exclude= mantém o deny
    cg.index()
    assert gs.find(user_id, project_id, "assina")["symbols"] == []


def test_write_file_reports_symbol_changes(fake_git_project):
    """`index()` agora conta o que mudou — a escrita repassa ao modelo, que pode
    chamar `impact` na hora em vez de adivinhar símbolo via diff."""
    user_id, project_id, root = fake_git_project
    gs._get_graph(user_id, project_id).index(True)  # índice base (app.py)
    out = gs.write_file(user_id, project_id, {}, "novo.py", "def criada():\n    return 1\n")
    ch = out.get("symbol_changes") or {}
    assert any("criada" in str(s) for s in ch.get("added", [])), ch


def test_signature_change_is_reported_on_edit(fake_git_project):
    user_id, project_id, root = fake_git_project
    gs.write_file(user_id, project_id, {}, "m.py", "def f(a):\n    return a\n")
    out = gs.edit_file(user_id, project_id, {}, "m.py", "def f(a):", "def f(a, b):")
    ch = out.get("symbol_changes") or {}
    assert ch.get("signature_changed"), ch


def test_unchanged_write_returns_no_symbol_changes(fake_git_project):
    """Commit sem mudança de símbolo (mesmo conteúdo/só corpo) não polui o
    retorno com um campo vazio."""
    user_id, project_id, root = fake_git_project
    gs.write_file(user_id, project_id, {}, "k.py", "def g():\n    return 1\n")
    out = gs.write_file(user_id, project_id, {}, "k.py", "def g():\n    return 2\n")
    assert "symbol_changes" not in out, out
