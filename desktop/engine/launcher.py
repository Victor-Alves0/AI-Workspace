"""Inicia o AI Workspace COMPLETO sem Docker (Linux): Postgres embarcado + UM processo
Python que serve a interface (arquivos estaticos) e a API numa porta so':
    http://localhost:41414/      interface
    http://localhost:41414/api   API

E' o equivalente do Start-AIWorkspace.ps1 (Windows), so' com a stdlib, rodado pelo
Python do proprio motor:  python/bin/python3 launcher.py [--data-dir DIR] [--no-browser]

Nada e instalado no sistema: banco, caches de modelo e o segredo do app ficam em
--data-dir (padrao: ./data ao lado deste arquivo). Apagar essa pasta = zerar.

Diferenca de proposito para o Windows: o motor pode estar num lugar SO' LEITURA (o
AppImage monta um squashfs). Por isso a atualizacao leve do codigo nao escreve por
cima do motor — vai para <data>/appcode/ e tem prioridade sobre o codigo embarcado.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
import urllib.request
import webbrowser
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = Path(sys.executable)
PG_BIN = ROOT / "pgsql" / "bin"
LOOP = "127.0.0.1"
# porta propria do AI Workspace (3000/8000 vivem ocupadas por servidores de dev)
APP_PORT = 41414
PG_PORT = 55432
PORT_SPAN = 20

# AIW_UPDATE_MANIFEST_URL: aponta para outro manifesto (teste local, canal de beta)
UPDATE_MANIFEST_URL = os.environ.get("AIW_UPDATE_MANIFEST_URL") or (
    "https://github.com/Victor-Alves0/AI-Workspace/releases/download/app-latest/app-update.json"
)


def log(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def port_free(p: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # o mesmo que o Postgres e o uvicorn fazem: sem isto, a porta que o motor
        # acabou de soltar (TIME_WAIT) parece ocupada e cada reabertura troca de porta.
        # No Linux nao libera bind numa porta em LISTEN, entao o teste segue honesto.
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((LOOP, p))
        except OSError:
            return False
    return True


def first_free(start: int) -> int:
    for p in range(start, start + PORT_SPAN):
        if port_free(p):
            return p
    raise SystemExit(f"nenhuma porta livre entre {start} e {start + PORT_SPAN - 1}")


def die_with_parent() -> None:
    """Pede ao kernel um SIGTERM quando o processo pai morrer (PR_SET_PDEATHSIG).

    Usado no launcher (pai = shell Tauri) e no uvicorn (pai = launcher): se o shell
    cair ou levar kill -9, a cadeia se encerra em vez de virar orfa segurando a porta.
    """
    with contextlib.suppress(Exception):
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG


# ----------------------------------------------------------------------------- #
# Codigo do app: o embarcado, ou a camada leve atualizada em <data>/appcode
# ----------------------------------------------------------------------------- #
def read_stamp(p: Path) -> str:
    try:
        return p.read_text(encoding="ascii").strip()
    except OSError:
        return ""


def code_dirs(data: Path) -> tuple[Path | None, Path, Path]:
    """(site-packages extra ou None, web, app) do codigo que vai rodar.

    O overlay so' vale para o motor que o gerou: `base.txt` guarda o app-version do
    motor de entao. Instalou um AppImage novo (codigo embarcado diferente) => o
    overlay velho e' descartado, senao ele mascararia o codigo novo do motor.
    """
    ov = data / "appcode"
    if (ov / "app-version.txt").exists():
        if read_stamp(ov / "base.txt") == read_stamp(ROOT / "app-version.txt"):
            return ov / "site-packages", ov / "web", ov / "app"
        shutil.rmtree(ov, ignore_errors=True)
    return None, ROOT / "web", ROOT / "app"


def update_app_code(data: Path) -> None:
    """Auto-atualizacao do CODIGO (camada leve). Nunca bloqueia o boot: qualquer
    falha (offline, manifesto/checksum invalido) e' ignorada."""
    if os.environ.get("AIW_NO_UPDATE") == "1" or (data / ".no-update").exists():
        return
    bundled_app = read_stamp(ROOT / "app-version.txt")
    engine = read_stamp(ROOT / "engine-version.txt")
    if not bundled_app or not engine:
        return  # bundle sem carimbo (build de dev)
    ov = data / "appcode"
    current = read_stamp(ov / "app-version.txt") if code_dirs(data)[0] else bundled_app

    try:
        with urllib.request.urlopen(UPDATE_MANIFEST_URL, timeout=8) as r:
            m = json.loads(r.read().decode("utf-8"))
    except Exception:
        log("Sem atualizacao (manifesto inacessivel)")
        return
    ver, url, sha = (str(m.get(k) or "").strip() for k in ("app_version", "url", "sha256"))
    if not (ver and url and sha) or ver == current:
        return
    if int(engine) < int(m.get("engine_required") or 0):
        log("Ha' uma atualizacao que exige um motor mais novo; instale o app completo para receber.")
        return

    log(f"Atualizando o app ({current} -> {ver})...")
    work = data / "_update"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        zpath = work / "app.zip"
        with urllib.request.urlopen(url, timeout=300) as r, open(zpath, "wb") as f:
            shutil.copyfileobj(r, f)
        h = hashlib.sha256()
        with open(zpath, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != sha.lower():
            raise RuntimeError("checksum nao confere")
        new = work / "appcode"
        with zipfile.ZipFile(zpath) as z:
            z.extractall(new)
        for sub in ("site-packages/aiworkspace", "web", "app"):
            if not (new / sub).is_dir():
                raise RuntimeError(f"pacote incompleto: falta {sub}")
        (new / "app-version.txt").write_text(ver, encoding="ascii")
        (new / "base.txt").write_text(bundled_app, encoding="ascii")
        # troca por rename: o overlay antigo so' sai depois que o novo esta' completo
        old = data / "appcode.old"
        shutil.rmtree(old, ignore_errors=True)
        if ov.exists():
            ov.rename(old)
        new.rename(ov)
        shutil.rmtree(old, ignore_errors=True)
        log(f"App atualizado para {ver}.")
    except Exception as exc:
        log(f"Falha ao atualizar (segue com o codigo atual): {exc}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ----------------------------------------------------------------------------- #
# Postgres
# ----------------------------------------------------------------------------- #
def pg(exe: str) -> str:
    return str(PG_BIN / exe)


def running_pg_port(pgdata: Path) -> int | None:
    """Porta de um Postgres NOSSO que ficou no ar (o launcher caiu sem pg_ctl stop).

    Reaproveitar e' melhor que falhar com "another server might be running": a 4a
    linha do postmaster.pid e' a porta.
    """
    pid_file = pgdata / "postmaster.pid"
    if not pid_file.exists():
        return None
    st = subprocess.run([pg("pg_ctl"), "-D", str(pgdata), "status"], capture_output=True)
    if st.returncode != 0:
        return None
    with contextlib.suppress(Exception):
        return int(pid_file.read_text().splitlines()[3])
    return None


def prepare_pgdata(pgdata: Path) -> None:
    if (pgdata / "PG_VERSION").exists():
        return
    tpl = ROOT / "pgdata-template.tar"
    tmp = pgdata.with_name(pgdata.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(pgdata, ignore_errors=True)
    if tpl.exists():
        log("Preparando o banco (primeira execucao)")
        # extrai ao lado e troca: extracao interrompida nao deixa pgdata pela metade
        tmp.mkdir(mode=0o700, parents=True)
        with tarfile.open(tpl) as t:
            t.extractall(tmp, filter="data")
        tmp.chmod(0o700)  # o Postgres recusa data dir com permissao de grupo/outros
        tmp.rename(pgdata)
    else:
        log("Inicializando o banco (primeira execucao)")
        subprocess.run(
            [pg("initdb"), "-D", str(pgdata), "-U", "aiworkspace", "-A", "trust",
             "-E", "UTF8", "--locale=C"],
            check=True, stdout=subprocess.DEVNULL,
        )


def start_pg(pgdata: Path, data: Path) -> int:
    """Sobe o Postgres so' no loopback e devolve a porta.

    Um Postgres que sobrou de um launcher que caiu e' adotado (e parado na saida):
    main() ja' saiu antes se havia um app vivo usando-o.
    """
    port = running_pg_port(pgdata)
    if port:
        log(f"Postgres ja' estava no ar em {LOOP}:{port}; reaproveitando")
        return port
    port = first_free(PG_PORT)
    log(f"Iniciando Postgres em {LOOP}:{port}")
    # unix_socket_directories='': so' TCP no loopback. O padrao (/tmp ou
    # /run/postgresql) pode nem existir/ser gravavel, e colidiria com um Postgres do SO.
    subprocess.run(
        [pg("pg_ctl"), "-D", str(pgdata), "-w", "-s", "-l", str(data / "pg.log"),
         "-o", f"-p {port} -c listen_addresses={LOOP} -c unix_socket_directories=''",
         "start"],
        check=True,
    )
    return port


def stop_pg(pgdata: Path) -> None:
    subprocess.run([pg("pg_ctl"), "-D", str(pgdata), "-w", "-s", "-m", "fast", "stop"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ensure_db(port: int) -> None:
    base = ["-h", LOOP, "-p", str(port), "-U", "aiworkspace"]
    existe = subprocess.run(
        [pg("psql"), *base, "-d", "postgres", "-tAc",
         "SELECT 1 FROM pg_database WHERE datname = 'aiworkspace'"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if existe != "1":
        subprocess.run([pg("createdb"), *base, "aiworkspace"], check=True)
    subprocess.run(
        [pg("psql"), *base, "-d", "aiworkspace", "-q", "-v", "ON_ERROR_STOP=1",
         "-c", "CREATE EXTENSION IF NOT EXISTS vector"],
        check=True,
    )


# ----------------------------------------------------------------------------- #
# Ambiente do backend: tudo apontando para dentro de <data>
# ----------------------------------------------------------------------------- #
def backend_env(data: Path, pg_port: int, app_port: int, site: Path | None, web: Path) -> dict:
    secret_file = data / "secret.txt"
    if not secret_file.exists():
        # deriva a chave que cifra os segredos no banco: gerado 1x, nunca trocado
        secret_file.write_text(secrets.token_urlsafe(48), encoding="ascii")
        secret_file.chmod(0o600)
    url = f"http://localhost:{app_port}"
    env = dict(os.environ)
    env.update({
        "APP_SECRET": secret_file.read_text(encoding="ascii").strip(),
        "DATABASE_URL": f"postgresql+asyncpg://aiworkspace:aiworkspace@{LOOP}:{pg_port}/aiworkspace",
        "FASTEMBED_CACHE_PATH": str(data / "cache" / "fastembed"),
        "HF_HOME": str(data / "cache" / "huggingface"),
        "CODESPACE_DATA_DIR": str(data / "codespace"),
        # interface e API na MESMA origem; as duas grafias do loopback
        "WEB_ORIGIN": f"{url},http://127.0.0.1:{app_port}",
        "AIW_WEB_DIR": str(web),
        "BROWSER_WS_URL": "local",
        "WHATSAPP_QR_BACKEND": "local",
        "WHATSAPP_LOCAL_DIR": str(data / "whatsapp"),
        "UPLOADS_DIR": str(data / "uploads"),
        "AIW_PORT": str(app_port),
        "OPENROUTER_REDIRECT_URI": f"{url}/api/integrations/providers/openrouter/callback",
        # pg_dump/psql do motor primeiro (backup pelo painel), depois o PATH do sistema
        "PATH": f"{PG_BIN}{os.pathsep}{os.environ.get('PATH', '')}",
    })
    for svc in ("google", "github", "notion", "slack"):
        env[f"{svc.upper()}_REDIRECT_URI"] = f"{url}/api/integrations/{svc}/callback"
    if site:
        # o overlay vem ANTES do site-packages do motor: o aiworkspace novo ganha
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (str(site), os.environ.get("PYTHONPATH", "")) if p
        )
    if not os.access(ROOT, os.W_OK):
        # motor so' leitura (AppImage): __pycache__ novo vai para <data>, nao falha calado
        env["PYTHONPYCACHEPREFIX"] = str(data / "cache" / "pycache")
    return env


def health(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{LOOP}:{port}/api/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Motor embarcado do AI Workspace (Linux)")
    ap.add_argument("--data-dir", default="", help="banco/caches/segredo (padrao: ./data)")
    ap.add_argument("--no-browser", action="store_true", help="nao abrir o navegador")
    ap.add_argument("--port", type=int, default=APP_PORT, help="porta preferida da interface")
    args = ap.parse_args()

    if os.geteuid() == 0:
        raise SystemExit("nao rode como root: o Postgres se recusa a subir como root")
    die_with_parent()

    data = Path(args.data_dir).resolve() if args.data_dir else ROOT / "data"
    pgdata = data / "pgdata"
    (data / "cache").mkdir(parents=True, exist_ok=True)
    port_file = data / "port.txt"
    # ja' ha' um motor vivo com estes dados (outra janela, ou o app desktop)? Nao sobe
    # outro — dividir o Postgres faria a 2a execucao derruba-lo ao sair.
    with contextlib.suppress(OSError, ValueError):
        live = int(port_file.read_text().strip())
        if health(live):
            log(f"AI Workspace ja' esta' no ar: http://localhost:{live}")
            if not args.no_browser:
                webbrowser.open(f"http://localhost:{live}")
            return 0
    port_file.unlink(missing_ok=True)

    app_port = first_free(args.port)
    if app_port != args.port:
        log(f"Porta {args.port} ocupada; usando {app_port}")

    try:
        update_app_code(data)
    except Exception as exc:
        log(f"Auto-update ignorado: {exc}")
    site, web, app_dir = code_dirs(data)

    prepare_pgdata(pgdata)
    pg_port = start_pg(pgdata, data)
    api: subprocess.Popen | None = None

    # SIGTERM (Tauri saindo / pai morto) vira o mesmo caminho limpo do Ctrl+C
    def on_term(signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGHUP, on_term)

    try:
        env = backend_env(data, pg_port, app_port, site, web)
        ensure_db(pg_port)
        log("Aplicando migracoes (alembic upgrade head)")
        subprocess.run([str(PY), "-m", "alembic", "upgrade", "head"],
                       cwd=app_dir, env=env, check=True)

        # a porta vai para o arquivo ANTES de subir: o shell desktop le' daqui
        port_file.write_text(str(app_port), encoding="ascii")
        log(f"Iniciando o AI Workspace em http://localhost:{app_port}")
        with open(data / "api.out.log", "ab") as out, open(data / "api.err.log", "ab") as err:
            api = subprocess.Popen(
                [str(PY), "-m", "uvicorn", "aiworkspace.desktop:app",
                 "--host", LOOP, "--port", str(app_port)],
                cwd=app_dir, env=env, stdout=out, stderr=err, preexec_fn=die_with_parent,
            )

        # 1o boot baixa o modelo de embeddings: pode demorar varios minutos
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline and api.poll() is None and not health(app_port):
            time.sleep(0.5)
        if api.poll() is not None:
            log(f"A API saiu (codigo {api.returncode}); veja {data / 'api.err.log'}")
            return 1
        if not args.no_browser:
            webbrowser.open(f"http://localhost:{app_port}")
        log(f"AI Workspace no ar: http://localhost:{app_port}   (Ctrl+C para encerrar)")
        return api.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        log("Encerrando...")
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if api and api.poll() is None:
            api.terminate()
            try:
                api.wait(timeout=15)
            except subprocess.TimeoutExpired:
                api.kill()
        port_file.unlink(missing_ok=True)
        stop_pg(pgdata)


if __name__ == "__main__":
    sys.exit(main())
