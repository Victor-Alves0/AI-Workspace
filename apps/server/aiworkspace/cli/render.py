"""Saída do CLI: a resposta em streaming, ferramentas e sub-agentes como linhas curtas.

Com `rich` instalado (extra [cli]) e saída num terminal, o markdown é renderizado ao
vivo; sem ele, sai o texto cru com cores ANSI. No modo `-p` (pipe), o stdout recebe SÓ
a resposta — ferramentas e avisos vão para o stderr, para `aiw -p ... > arquivo`.
"""
from __future__ import annotations

import json
import os
import sys

try:  # opcional: o motor antigo (sem o extra [cli]) ainda roda o CLI, só mais simples
    from rich.console import Console
    from rich.live import Live
    from rich.markdown import Markdown
except ImportError:  # pragma: no cover - depende do motor instalado
    Console = None  # type: ignore[assignment,misc]


def _color_ok(stream) -> bool:
    return stream.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"


class Style:
    def __init__(self, stream) -> None:
        on = _color_ok(stream)
        self.dim = "\033[2m" if on else ""
        self.bold = "\033[1m" if on else ""
        self.red = "\033[31m" if on else ""
        self.cyan = "\033[36m" if on else ""
        self.magenta = "\033[35m" if on else ""
        self.reset = "\033[0m" if on else ""


def _short(value, limit: int = 90) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    s = " ".join(str(value).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _args_preview(args) -> str:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return _short(args, 60)
    if isinstance(args, dict):
        parts = [f"{k}={_short(v, 40)}" for k, v in list(args.items())[:3]]
        return ", ".join(parts)
    return _short(args, 60)


class TurnView:
    """Renderiza os eventos de UMA geração. `pipe=True`: modo -p."""

    def __init__(self, pipe: bool = False) -> None:
        self.pipe = pipe
        self.out = sys.stdout
        self.side = sys.stderr if pipe else sys.stdout  # ferramentas/avisos
        self.st = Style(self.side)
        self.rich = Console is not None and not pipe and sys.stdout.isatty()
        self.console = Console(highlight=False) if self.rich else None
        self.live = None
        self.buf = ""  # texto do segmento atual (entre ferramentas)
        self.text = ""  # resposta inteira
        self.at_bol = True  # a saída de texto está no começo de uma linha
        self.thinking = False
        self.title: str | None = None
        self.usage: dict | None = None
        self.error: str | None = None
        self.stopped = False

    # -- texto ------------------------------------------------------------------
    def _token(self, t: str) -> None:
        if not t:
            return
        self._end_thinking()
        self.buf += t
        self.text += t
        if self.rich:
            if self.live is None:
                self.live = Live(Markdown(self.buf), console=self.console,
                                 refresh_per_second=10, vertical_overflow="visible")
                self.live.start()
            else:
                self.live.update(Markdown(self.buf))
        else:
            self.out.write(t)
            self.out.flush()
            self.at_bol = t.endswith("\n")

    def _flush_segment(self) -> None:
        """Fecha o bloco de texto antes de uma linha de ferramenta."""
        if self.live is not None:
            self.live.update(Markdown(self.buf))
            self.live.stop()
            self.live = None
        elif not self.at_bol:
            self.out.write("\n")
            self.at_bol = True
        self.buf = ""

    def _side(self, line: str) -> None:
        self._flush_segment()
        self._end_thinking()
        self.side.write(line + "\n")
        self.side.flush()

    def _end_thinking(self) -> None:
        if self.thinking:
            self.thinking = False

    # -- eventos ----------------------------------------------------------------
    def handle(self, ev: dict) -> bool:
        """Processa um evento. Devolve False quando a geração terminou."""
        t = ev.get("type")
        st = self.st
        if t == "token":
            self._token(ev.get("text") or "")
        elif t == "reasoning":
            if not self.thinking and not self.buf:  # uma linha só, não o raciocínio inteiro
                self._side(f"{st.dim}✻ pensando…{st.reset}")
                self.thinking = True
        elif t == "tool_call":
            name = ev.get("name") or "?"
            self._side(f"{st.cyan}⏺{st.reset} {st.bold}{name}{st.reset}"
                       f"{st.dim}({_args_preview(ev.get('arguments'))}){st.reset}")
        elif t == "tool_result":
            res = ev.get("result")
            if isinstance(res, dict) and res.get("error"):
                self._side(f"  {st.dim}⎿{st.reset} {st.red}{_short(res.get('error'))}{st.reset}")
            else:
                self._side(f"  {st.dim}⎿ {_short(res) if res not in (None, '') else 'ok'}{st.reset}")
        elif t == "subagent":
            s = ev.get("status") or ""
            who = ev.get("agent") or ev.get("team") or "agente"
            if s in ("start", "team_start"):
                task = ev.get("task") or ev.get("goal") or ""
                self._side(f"{st.magenta}◆{st.reset} {st.bold}{who}{st.reset} {st.dim}{_short(task, 70)}{st.reset}")
            elif s in ("done", "team_done"):
                self._side(f"  {st.dim}⎿ {who} terminou{st.reset}")
            elif s in ("background", "team_background"):
                self._side(f"  {st.dim}⎿ {who} segue em segundo plano{st.reset}")
        elif t == "error":
            self.error = str(ev.get("message") or "erro")
            self._side(f"{st.red}erro: {self.error}{st.reset}")
        elif t == "title":
            self.title = ev.get("title") or self.title
        elif t == "usage":
            self.usage = ev.get("usage") or self.usage
        elif t == "stopped":
            self.stopped = True
        elif t in ("done", "idle"):
            return False
        return True

    def finish(self) -> None:
        self._flush_segment()
        st = self.st
        if self.stopped:
            self.side.write(f"{st.dim}(interrompido){st.reset}\n")
        if not self.pipe and self.usage:
            u = self.usage
            tok = u.get("total_tokens") or 0
            cost = u.get("cost")
            bits = [f"{tok / 1000:.1f}k tokens" if tok >= 1000 else f"{tok} tokens"] if tok else []
            if isinstance(cost, (int, float)) and cost:
                bits.append(f"${cost:.4f}")
            if bits:
                self.side.write(f"{st.dim}· {' · '.join(bits)}{st.reset}\n")
        self.out.flush()
        self.side.flush()


def print_message(role: str, content: str, pipe: bool = False) -> None:
    """Mensagem já gravada (histórico ao retomar um chat)."""
    st = Style(sys.stdout)
    if role == "user":
        first, *rest = (content or "").splitlines() or [""]
        sys.stdout.write(f"{st.bold}›{st.reset} {first}\n")
        for line in rest:
            sys.stdout.write(f"  {line}\n")
        return
    if Console is not None and sys.stdout.isatty() and not pipe:
        Console(highlight=False).print(Markdown(content or ""))
    else:
        sys.stdout.write((content or "") + "\n")
