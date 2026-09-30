"""run_code (code mode da SIFT) no executor isolado.

Mesmo protocolo do `SubprocessSandbox` da SIFT — o filho `_sandbox_child.py` roda o
trecho e pede cada ferramenta de volta ao pai por stdin/stdout —, só que o filho nasce
no container `runner` (via `execution.RemoteProc`) em vez de ao lado do servidor. As
ferramentas continuam executando AQUI, no pai, com as credenciais do usuário; o código
gerado pelo modelo nunca encosta nelas.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import threading

from sift.sandbox import SubprocessSandbox

from .. import execution


class RunnerSandbox(SubprocessSandbox):
    def run(self, code: str, call, search, schema) -> str:  # noqa: C901 - espelha a SIFT
        handlers = {
            "call": lambda m: call(m["path"], **(m.get("params") or {})),
            "search": lambda m: search(m["q"], m.get("top_k", 5)),
            "schema": lambda m: schema(m["path"]),
        }
        import sift.sandbox as _sb
        child = os.path.join(os.path.dirname(os.path.abspath(_sb.__file__)), "_sandbox_child.py")
        try:
            proc = execution.RemoteProc(
                argv=[sys.executable, child], cwd="/tmp", env={"PYTHONIOENCODING": "utf-8"},
                cpu_seconds=int(self.cpu_seconds), mem_mb=int(self.memory_mb),
                stdin=True, merge_stderr=False,
            )
        except OSError as exc:
            return json.dumps({"error": f"SandboxError: {exc}"}, ensure_ascii=False)

        stderr_tail: list[str] = []

        def _drain():
            with contextlib.suppress(Exception):
                for line in proc.stderr:
                    stderr_tail.append(line)
                    del stderr_tail[:-20]

        threading.Thread(target=_drain, daemon=True).start()
        killed = {"v": False}

        def _watchdog():
            killed["v"] = True
            proc.kill()

        timer = threading.Timer(self.timeout, _watchdog)
        timer.start()
        try:
            proc.stdin.write(json.dumps({"code": code, "max_lines": self.max_lines}) + "\n")
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                msg = json.loads(line)
                if msg.get("op") == "done":
                    return msg["result"]
                # o relógio pausa enquanto a ferramenta (confiável) roda aqui no pai
                timer.cancel()
                try:
                    value = handlers[msg.get("op")](msg)
                    proc.stdin.write(json.dumps({"ok": True, "value": value}, default=str) + "\n")
                except Exception as exc:  # noqa: BLE001 - erro volta ao trecho
                    proc.stdin.write(json.dumps({"ok": False, "error": str(exc)}) + "\n")
                finally:
                    timer = threading.Timer(self.timeout, _watchdog)
                    timer.start()
        finally:
            timer.cancel()
            proc.kill()
        if killed["v"]:
            return json.dumps({"error": "SandboxError: wall-clock timeout"})
        with contextlib.suppress(Exception):
            proc.wait(0.5)
        detail = ("".join(stderr_tail).strip()[-500:]) or "no stderr output"
        return json.dumps({"error": f"SandboxError: sandbox child exited unexpectedly ({detail})"},
                          ensure_ascii=False)
