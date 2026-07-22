"""Codespace: path-jail + escopo allow/deny (o vetor de segurança das tools
code.files.browse), truncamento de assinatura e o header de autenticação do
clone git. Puro/hermético — sem DB, sem CodeGraph real (monkeypatcha _DATA_ROOT
com um diretório de arquivos reais em tmp_path)."""
from __future__ import annotations

import base64

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


def test_auth_header_never_in_git_config(fake_git_project):
    """O token do push NUNCA fica gravado em .git/config (usamos -c http.extraHeader,
    não a URL) — só confirmamos que o commit normal não grava nada parecido."""
    user_id, project_id, root = fake_git_project
    gs.write_file(user_id, project_id, {}, "x.py", "1")
    cfg = (root / ".git" / "config").read_text()
    assert "token" not in cfg.lower()
    assert "authorization" not in cfg.lower()
