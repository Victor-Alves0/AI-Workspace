"""Bateria de classificação de comando: _looks_like_server / _is_long_runner.

Estas funções decidem se um comando SOBE UM SERVIDOR (→ recusa, manda pro preview.serve)
ou é LONGO (→ background). Antes, o regex casava o token em QUALQUER posição da linha —
inclusive DENTRO de argumentos/aspas/nomes de arquivo — e:
  - _looks_like_server falso-positivo → RECUSAVA um comando legítimo (`cat vite.config.js`);
  - _is_long_runner falso-positivo → jogava pro background à toa.
Corrigido classificando pelo COMANDO REAL (shlex + split por operadores + tira VAR=val/wrappers
+ olha executável/args posicionais). Esta bateria trava o comportamento e os casos DIFÍCEIS.
"""
from __future__ import annotations

from aiworkspace.tools.sift_service import _looks_like_server, _is_long_runner, _split_commands


# ------------------------------ SERVIDOR: positivos ---------------------------
def test_server_positivos():
    for cmd in [
        # o comando EXATO do bug do Metabase, inclusive com cd && e env vars na frente
        "clojure -M:run:dev:dev-start",
        'cd "$PWD" && MB_DB_TYPE=h2 HOST=0.0.0.0 PORT=4001 clojure -M:run:dev:dev-start',
        "npm run dev", "npm start", "yarn dev", "pnpm run serve",
        "next dev", "next start -p 3000", "vite", "vite --host 0.0.0.0", "ng serve", "astro dev",
        "python manage.py runserver 0.0.0.0:8000", "flask run --host 0.0.0.0",
        "uvicorn app:app --host 0.0.0.0", "gunicorn wsgi:app", "./gradlew bootRun",
        "rails server", "php -S 0.0.0.0:8000", "dotnet run", "mvn spring-boot:run",
        # wrappers na frente (nohup/env) não escondem o servidor
        "nohup npm run dev", "env FOO=bar uvicorn a:b",
    ]:
        assert _looks_like_server(cmd), cmd


# ------------------------------ SERVIDOR: negativos ---------------------------
def test_server_negativos_builds_e_scripts():
    for cmd in ["npm install", "npm run build", "npm run test", "mvn -q test",
                "./gradlew build", "pip install requests", "cargo build", "pytest -k auth",
                "clojure -M:build", "python script.py", "ls -la", "git status",
                "next build", "vite build", "npm run dev-setup"]:
        assert not _looks_like_server(cmd), cmd


# --- SERVIDOR: falsos positivos que ERAM recusados (o bug) — agora NÃO servidor ---
def test_server_nao_dispara_dentro_de_argumentos():
    for cmd in [
        "cat vite.config.js", "cat gunicorn.conf",
        'git commit -m "add npm run dev script"', "echo npm start",
        "ls -la | grep uvicorn", "grep -rn rails server src/",
        'sed -i "s/next dev/next build/" package.json',
        'echo "starting uvicorn server"', 'python -c "import x; x.run()"',
        'cat x | grep "npm run dev"',
    ]:
        assert not _looks_like_server(cmd), f"recusaria um comando inocente: {cmd}"


# ------------------------------ LONGO: positivos ------------------------------
def test_long_runner_positivos():
    for cmd in [
        "mvn -q test".replace("mvn", "mvn"), "mvn -q test", "mise use -g java@21 maven",
        "npm install", "pnpm i", "pip install requests", "cargo build", "go mod download",
        "git clone https://x/y", "./gradlew build", "clojure -P", "wget https://x/y.jar",
        "apt-get install foo", "poetry add bar", "uv pip install requests",
        "npm run lint", "pytest -k auth", "npm run build",
    ]:
        assert _is_long_runner(cmd), cmd


# ------------------------------ LONGO: negativos ------------------------------
def test_long_runner_negativos():
    for cmd in ["ls -la", "cat pom.xml", "grep -r foo src", "python script.py",
                "echo hi", "git status", "npm run dev", "cat requirements.txt"]:
        assert not _is_long_runner(cmd), cmd


# --- LONGO: falsos positivos que jogavam pro bg à toa — agora NÃO longo ---
def test_long_runner_nao_dispara_dentro_de_argumentos():
    for cmd in [
        "cat requirements.txt", "echo git clone done",
        'git commit -m "run npm install first"', "grep -rn 'pip install' docs/",
    ]:
        assert not _is_long_runner(cmd), f"jogaria pro background à toa: {cmd}"


# ------------------------------ Casos DIFÍCEIS --------------------------------
def test_pipelines_e_compostos():
    # pipeline: classifica pelos sub-comandos reais
    assert not _looks_like_server('cat x | grep "npm run dev"')
    # composto com install + serve → é servidor (o serve manda) E é longo (o install)
    assert _looks_like_server("npm install && npm run dev")
    assert _is_long_runner("npm install && npm run dev")
    # composto só de builds → longo, não servidor
    assert _is_long_runner("npm ci && npm run build")
    assert not _looks_like_server("npm ci && npm run build")


def test_aspas_desbalanceadas_nao_crasham_e_sao_conservadoras():
    # parse falha (aspa aberta) → None internamente → classificadores retornam False
    # (jamais RECUSA por ambiguidade; um comando malformado erra rápido no shell)
    for cmd in ['echo "unbalanced', "grep 'oops", 'run "npm run dev']:
        assert _split_commands(cmd) is None
        assert not _looks_like_server(cmd)
        assert not _is_long_runner(cmd)


def test_entrada_vazia_ou_estranha():
    for cmd in ["", "   ", None]:
        assert not _looks_like_server(cmd or "")
        assert not _is_long_runner(cmd or "")


def test_vite_flag_antes_de_build_nao_e_servidor():
    # vite serve por padrão, MAS build/optimize (mesmo com flag antes) NÃO é servidor
    assert _looks_like_server("vite")
    assert _looks_like_server("vite --host 0.0.0.0")
    assert _looks_like_server("vite preview")
    assert not _looks_like_server("vite build")
    assert not _looks_like_server("vite build --watch")
    assert not _looks_like_server("vite --config vite.dev.js build")  # flag antes do build
    assert not _looks_like_server("vite optimize")


def test_redirecionamentos_de_shell_nao_confundem():
    # `>`/`2>&1` viram separadores; o comando real é que classifica
    assert _is_long_runner("npm run build > build.log")
    assert not _looks_like_server("npm run build > build.log")
    assert _is_long_runner("pytest -k auth 2>&1")
    assert _looks_like_server("uvicorn app:app > srv.log 2>&1")
    assert not _looks_like_server("echo done > uvicorn.log")  # 'uvicorn' é só o arquivo de saída
