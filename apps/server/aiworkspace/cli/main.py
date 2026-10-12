"""`aiw`: o AI Workspace no terminal.

    aiw                      chat novo, interativo
    aiw "pergunta"           chat novo começando com esta mensagem
    aiw -c                   continua o último chat usado
    aiw -r [ID|texto]        retoma um chat (sem valor: lista para escolher)
    aiw -p "pergunta"        responde e sai (aceita pipe: cat x | aiw -p "resuma")
    aiw -m MODELO ...        modelo do chat novo (padrão: o do seu perfil)
    aiw ui                   abre o app desktop
    aiw chats | models       listagens
    aiw install              instala o comando `aiw` em ~/.local/bin (Linux)

Os chats são os MESMOS do app: um chat começado aqui aparece na janela e vice-versa.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .client import Api, CliError, Engine, data_dir, engine_root
from .render import Style, TurnView, print_message

HELP_REPL = """\
/new            chat novo          /chats         lista os chats recentes
/resume N|ID    retoma um chat     /model [ID]    mostra/troca o modelo do chat
/exit           sai (ou Ctrl+D)    Ctrl+C         interrompe a resposta
Linha terminando em \\ continua na próxima."""


def _title(chat: dict) -> str:
    # o título provisório é o começo da 1a mensagem: pode ter quebras de linha (pipe)
    t = " ".join((chat.get("title") or "").split()) or "Sem título"
    return t if len(t) <= 60 else t[:59] + "…"


def _ago(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    secs = (datetime.now(dt.tzinfo) - dt).total_seconds()
    for unit, n in (("d", 86400), ("h", 3600), ("min", 60)):
        if secs >= n:
            return f"há {int(secs // n)}{unit}"
    return "agora"


class Session:
    def __init__(self, api: Api, me: dict, model: str | None, pipe: bool) -> None:
        self.api = api
        self.me = me
        self.model = model  # modelo para o PRÓXIMO chat novo (None = padrão do perfil)
        self.pipe = pipe
        self.chat: dict | None = None
        self.st = Style(sys.stderr if pipe else sys.stdout)

    # -- chats ------------------------------------------------------------------
    def recent(self) -> list[dict]:
        rows = self.api.call("GET", "/chats") or []
        rows = [c for c in rows if c.get("mode", "single") == "single" and not c.get("view_once")]
        # "último usado" = última mensagem (a API põe os fixados no topo e ordena pelo
        # updated_at, que não muda a cada mensagem). Motor antigo: sem o campo.
        return sorted(rows, key=lambda c: c.get("last_message_at") or c.get("updated_at") or "",
                      reverse=True)

    def find(self, ref: str) -> dict:
        rows = self.recent()
        if ref.isdigit() and 1 <= int(ref) <= len(rows):
            return rows[int(ref) - 1]
        for c in rows:
            if c["id"] == ref or c["id"].startswith(ref):
                return c
        low = ref.lower()
        hits = [c for c in rows if low in (c.get("title") or "").lower()]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise CliError(f"'{ref}' bate com {len(hits)} chats; use o número de `aiw chats`")
        raise CliError(f"nenhum chat com '{ref}'")

    def list_chats(self, limit: int = 15) -> list[dict]:
        rows = self.recent()[:limit]
        st = self.st
        for i, c in enumerate(rows, 1):
            pin = "📌 " if c.get("pinned") else ""
            print(f"{st.dim}{i:>3}{st.reset}  {pin}{_title(c)}"
                  f"  {st.dim}{_ago(c.get('last_message_at') or c.get('updated_at') or '')} · {c.get('model') or 'modelo do agente'}{st.reset}")
        if not rows:
            print(f"{st.dim}nenhum chat ainda{st.reset}")
        return rows

    def open(self, chat: dict, history: int = 6) -> None:
        self.chat = chat
        st = self.st
        print(f"{st.dim}── {_title(chat)} · {chat.get('model') or ''}{st.reset}")
        msgs = self.api.call("GET", f"/chats/{chat['id']}/messages") or []
        msgs = [m for m in msgs if m.get("role") in ("user", "assistant") and not m.get("is_summary")]
        if len(msgs) > history:
            print(f"{st.dim}… {len(msgs) - history} mensagens anteriores{st.reset}")
        for m in msgs[-history:]:
            print_message(m["role"], m.get("content") or "")
        # a resposta ainda está sendo gerada (ex.: começou no app)? acompanha ao vivo.
        # Só quando a última mensagem gravada é do usuário: a do assistente só é gravada
        # no fim, e o servidor guarda a geração CONCLUÍDA por um tempo — reassiná-la
        # repetiria na tela a resposta que o histórico acabou de mostrar.
        if msgs and msgs[-1]["role"] == "user":
            self._watch(self.api.stream("GET", f"/chats/{chat['id']}/stream"))

    def new_chat(self) -> dict:
        model = self.model or self.me.get("default_model") or ""
        if not model:
            raise CliError("sem modelo: use `aiw -m MODELO` (veja `aiw models`) ou defina um padrão no app")
        return self.api.call("POST", "/chats", json={"model": model})

    # -- turno ------------------------------------------------------------------
    def _watch(self, events) -> TurnView:
        view = TurnView(pipe=self.pipe)
        try:
            for ev in events:
                if not view.handle(ev):
                    break
        except KeyboardInterrupt:
            if self.chat:  # o "Parar" da interface: o parcial fica gravado
                try:
                    self.api.call("POST", f"/chats/{self.chat['id']}/stop")
                except CliError:
                    pass
            view.stopped = True
        view.finish()
        if view.title and self.chat:
            self.chat["title"] = view.title
        return view

    def send(self, text: str) -> TurnView:
        if self.chat is None:  # o chat só nasce na 1a mensagem: nada de chats vazios
            self.chat = self.new_chat()
        return self._watch(self.api.stream(
            "POST", f"/chats/{self.chat['id']}/messages", json={"content": text}))

    # -- REPL -------------------------------------------------------------------
    def repl(self) -> None:
        st = self.st
        _setup_readline(self.api.data)
        print(f"{st.dim}AI Workspace · /help para comandos · Ctrl+D sai{st.reset}")
        prompt = _prompt(st)
        while True:
            try:
                text = _read_message(prompt)
            except EOFError:
                print()
                return
            except KeyboardInterrupt:
                print()
                continue
            if not text.strip():
                continue
            if text.startswith("/"):
                if self._command(text.strip()) is False:
                    return
                continue
            try:
                self.send(text)
            except CliError as exc:
                print(f"{st.red}erro: {exc}{st.reset}")

    def _command(self, line: str) -> bool | None:
        cmd, _, arg = line.partition(" ")
        arg = arg.strip()
        st = self.st
        try:
            if cmd in ("/exit", "/quit", "/sair"):
                return False
            if cmd == "/help":
                print(HELP_REPL)
            elif cmd == "/new":
                self.chat = None
                print(f"{st.dim}── chat novo{st.reset}")
            elif cmd == "/chats":
                self.list_chats()
            elif cmd == "/resume":
                if not arg:
                    rows = self.list_chats()
                    if not rows:
                        return None
                    arg = input("número do chat: ").strip()
                    if not arg:
                        return None
                self.open(self.find(arg))
            elif cmd == "/model":
                if not arg:
                    cur = (self.chat or {}).get("model") or self.model or self.me.get("default_model")
                    print(f"modelo: {cur or '(nenhum)'}")
                elif self.chat is None:
                    self.model = arg
                    print(f"{st.dim}o próximo chat usa {arg}{st.reset}")
                else:
                    self.chat = self.api.call("PATCH", f"/chats/{self.chat['id']}", json={"model": arg})
                    print(f"{st.dim}modelo do chat: {arg}{st.reset}")
            else:
                print(f"comando desconhecido: {cmd} (/help)")
        except CliError as exc:
            print(f"{st.red}erro: {exc}{st.reset}")
        except (EOFError, KeyboardInterrupt):
            print()
        return None


def _prompt(st: Style) -> str:
    """O `›` do prompt. Cor só com a GNU readline, com as marcas \001/\002 que dizem
    a ela que os códigos ANSI não ocupam coluna (sem elas o histórico redesenha
    torto). A libedit — a do Python embarcado — engole o ESC e mostraria `[1m›`."""
    try:
        import readline
    except ImportError:
        return "› "
    if not st.bold or "libedit" in (readline.__doc__ or ""):
        return "› "
    return f"\001{st.bold}\002›\001{st.reset}\002 "


def _setup_readline(data: Path) -> None:
    try:
        import atexit
        import readline
    except ImportError:  # Windows sem pyreadline: input() simples
        return
    hist = data / "cli-history"
    try:
        readline.read_history_file(hist)
    except OSError:
        pass
    readline.set_history_length(1000)
    atexit.register(lambda: _save_history(readline, hist))


def _save_history(readline, hist: Path) -> None:
    try:
        readline.write_history_file(hist)
    except OSError:
        pass


def _read_message(prompt: str) -> str:
    lines = [input(prompt)]
    while lines[-1].endswith("\\"):
        lines[-1] = lines[-1][:-1]
        lines.append(input("  "))
    return "\n".join(lines)


# ----------------------------------------------------------------------------- #
# subcomandos que não precisam de sessão
# ----------------------------------------------------------------------------- #
def _desktop_bin() -> list[str] | None:
    if v := os.environ.get("AIW_DESKTOP_BIN"):  # o shell desktop/AppImage informa
        return [v]
    root = engine_root()
    cands = []
    if root is not None:
        # .deb: /usr/lib/AI Workspace/engine -> /usr/bin/aiworkspace-desktop
        cands.append(root.parent.parent.parent / "bin" / "aiworkspace-desktop")
        # Windows: o .exe fica na pasta de instalação, ao lado de engine\
        cands.append(root.parent / "aiworkspace-desktop.exe")
    found = shutil.which("aiworkspace-desktop")
    if found:
        cands.append(Path(found))
    for c in cands:
        if c.exists():
            return [str(c)]
    return None


def cmd_ui() -> int:
    exe = _desktop_bin()
    if not exe:
        raise CliError("app desktop não encontrado")
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kw["creationflags"] = 0x0000_0008  # DETACHED_PROCESS
    else:
        kw["start_new_session"] = True  # sobrevive ao terminal
    subprocess.Popen(exe, **kw)
    return 0


def cmd_install() -> int:
    if sys.platform == "win32":
        raise CliError("no Windows o instalador já põe o `aiw` no PATH")
    appimage = os.environ.get("AIW_APPIMAGE")
    root = engine_root()
    if appimage:
        target = f'exec "{appimage}" aiw "$@"'
    elif root is not None:
        target = f'exec "{root / "aiw"}" "$@"'
    else:
        raise CliError("rode o install pelo `aiw` do app")
    bindir = Path.home() / ".local" / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    dest = bindir / "aiw"
    dest.write_text(f"#!/bin/sh\n# AI Workspace CLI (gerado por `aiw install`)\n{target}\n")
    dest.chmod(0o755)
    print(f"instalado: {dest}")
    if str(bindir) not in os.environ.get("PATH", "").split(os.pathsep):
        print('adicione ao PATH: export PATH="$HOME/.local/bin:$PATH"  (no ~/.bashrc ou ~/.zshrc)')
    return 0


def cmd_models(api: Api) -> int:
    rows: list[dict] = []
    for path in ("/settings/models", "/integrations/ollama/models"):
        try:
            rows += api.call("GET", path) or []
        except CliError:
            pass
    st = Style(sys.stdout)
    for m in rows:
        print(f"{m.get('id')}  {st.dim}{m.get('provider') or ''}{st.reset}")
    if not rows:
        print("nenhum modelo: conecte um provedor no app (Configurações → Conexões)")
    return 0


# ----------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aiw", description="AI Workspace no terminal.",
                                epilog="subcomandos: ui, chats, models, install")
    p.add_argument("prompt", nargs="*", help="mensagem inicial")
    p.add_argument("-p", "--print", dest="print_mode", action="store_true",
                   help="responde e sai (stdin em pipe vira contexto)")
    p.add_argument("-c", "--continue", dest="cont", action="store_true", help="continua o último chat")
    p.add_argument("-r", "--resume", nargs="?", const="", default=None, metavar="CHAT",
                   help="retoma um chat (número, id ou parte do título)")
    p.add_argument("-m", "--model", help="modelo do chat novo")
    p.add_argument("--version", action="store_true")
    return p


def run(argv: list[str]) -> int:
    if argv and argv[0] == "ui":
        return cmd_ui()
    if argv and argv[0] == "install":
        return cmd_install()
    sub = argv[0] if argv and argv[0] in ("chats", "models") else None
    args = build_parser().parse_args(argv[1:] if sub else argv)
    if args.version:
        from .. import __version__
        print(f"aiw {__version__}")
        return 0

    # só quem envia mensagem lê o stdin: `aiw chats` num stdin aberto e sem fim (cron,
    # IDE, job em segundo plano) ficaria esperando um EOF que nunca chega
    piped = None if sub or sys.stdin.isatty() else sys.stdin.read()
    # stdin em pipe = modo -p: o REPL leria de um stdin já consumido
    pipe = args.print_mode or piped is not None
    st = Style(sys.stderr)

    engine = Engine(data_dir())
    try:
        def status(msg: str) -> None:
            if sys.stderr.isatty():
                sys.stderr.write(f"\r{st.dim}{msg}{st.reset}\033[K")
                sys.stderr.flush()

        port = engine.ensure(status)
        if sys.stderr.isatty():
            sys.stderr.write("\r\033[K")
        api = Api(port, data_dir())
        me = api.login()
        if sub == "chats":
            Session(api, me, None, False).list_chats(50)
            return 0
        if sub == "models":
            return cmd_models(api)

        s = Session(api, me, args.model, pipe)
        if args.cont:
            rows = s.recent()
            if not rows:
                raise CliError("nenhum chat para continuar")
            s.chat = rows[0]
        elif args.resume is not None:
            if args.resume:
                s.chat = s.find(args.resume)
            elif not pipe:
                rows = s.list_chats()
                if not rows:
                    return 0
                s.chat = s.find(input("número do chat: ").strip() or "1")
            else:
                raise CliError("--resume sem chat não funciona em pipe; passe o número ou id")

        text = " ".join(args.prompt).strip()
        if piped:
            text = f"{text}\n\n```\n{piped.rstrip()}\n```" if text else piped
        if pipe:
            if not text:
                raise CliError("nada para enviar: aiw -p \"pergunta\"")
            view = s.send(text)
            return 1 if view.error else 0
        if s.chat is not None:
            s.open(s.chat)
        if text:
            s.send(text)
        s.repl()
        return 0
    except CliError as exc:
        sys.stderr.write(f"{st.red}aiw: {exc}{st.reset}\n")
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        engine.close()


def main() -> None:
    try:
        code = run(sys.argv[1:])
        sys.stdout.flush()
    except BrokenPipeError:
        # `aiw -p ... | head`: o leitor fechou o pipe. Sai calado (a geração segue e é
        # gravada no servidor); o devnull evita o 2o erro no flush da saída do Python.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        code = 141
    sys.exit(code)
