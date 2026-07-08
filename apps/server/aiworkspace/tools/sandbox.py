"""Execução isolada do código de tools do usuário.

O código do usuário NÃO roda no processo do servidor. Cada execução acontece em
um subprocesso Python isolado (`python -I`), com:
  - timeout rígido (mata o processo se estourar)
  - limites de CPU e memória (RLIMIT_*) quando o SO suporta (Linux/containers)
  - comunicação por stdin/stdout em JSON (sem acesso ao estado do servidor)

Isso contém código malicioso/com bug: não derruba o servidor, não consome
recursos sem limite e não enxerga conexões/segredos do processo principal.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from ..config import get_settings

# Runner executado dentro do subprocesso. Lê {code, params} de stdin, executa o
# código do usuário, chama run(**params) e devolve {ok, result|error} em stdout.
_LIMIT = r"""
import os
try:
    import resource
    def _limit():
        try:
            soft_cpu = int(os.environ.get("TOOL_CPU", "5"))
            soft_mem = int(os.environ.get("TOOL_MEM_MB", "256")) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_CPU, (soft_cpu, soft_cpu))
            resource.setrlimit(resource.RLIMIT_AS, (soft_mem, soft_mem))
        except Exception:
            pass
    _limit()
except Exception:
    pass  # SO sem suporte (ex.: Windows); o timeout do pai ainda protege
"""

# Runner de execução: injeta `valves` (defaults do VALVES + overrides) e chama run(**params).
_RUNNER = _LIMIT + r"""
import json, sys
payload = json.load(sys.stdin)
ns = {"valves": {}}
try:
    exec(compile(payload["code"], "<tool>", "exec"), ns)
    defaults = ns.get("VALVES", {})
    if not isinstance(defaults, dict):
        defaults = {}
    ns["valves"] = {**defaults, **(payload.get("valves") or {})}
    fn = ns.get("run")
    if not callable(fn):
        raise ValueError("o codigo precisa definir `def run(...)`")
    result = fn(**payload.get("params", {}))
    json.dump({"ok": True, "result": result}, sys.stdout, default=str)
except Exception as exc:
    json.dump({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, sys.stdout)
"""

# Runner de extração: retorna o dict VALVES declarado no código (defaults das valves).
_EXTRACT = _LIMIT + r"""
import json, sys
payload = json.load(sys.stdin)
ns = {}
try:
    exec(compile(payload["code"], "<tool>", "exec"), ns)
    valves = ns.get("VALVES", {})
    if not isinstance(valves, dict):
        valves = {}
    json.dump({"ok": True, "result": valves}, sys.stdout, default=str)
except Exception as exc:
    json.dump({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, sys.stdout)
"""


class ToolExecutionError(Exception):
    pass


def _exec(runner: str, payload: dict[str, Any]) -> Any:
    s = get_settings()
    env = {
        "TOOL_CPU": str(s.tool_cpu_seconds),
        "TOOL_MEM_MB": str(s.tool_mem_mb),
        "PATH": "/usr/bin:/bin",
    }
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", runner],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=s.tool_timeout_seconds,
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise ToolExecutionError(
            f"timeout: a ferramenta excedeu {s.tool_timeout_seconds}s"
        )

    if proc.returncode != 0 and not proc.stdout:
        # returncode negativo = morto por sinal (ex.: -9 SIGKILL por limite de CPU/memória)
        if proc.returncode < 0:
            raise ToolExecutionError(
                "a ferramenta foi interrompida por exceder o limite de CPU/memória"
            )
        err = (proc.stderr or "").strip()[-500:]
        raise ToolExecutionError(f"falha na execução (código {proc.returncode}): {err}")

    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise ToolExecutionError("a ferramenta não retornou JSON válido")

    if not out.get("ok"):
        raise ToolExecutionError(out.get("error", "erro desconhecido"))
    return out["result"]


def run_in_subprocess(code: str, params: dict[str, Any], valves: dict[str, Any] | None = None) -> Any:
    """Executa run(**params) no sandbox, com `valves` disponível como global."""
    return _exec(_RUNNER, {"code": code, "params": params, "valves": valves or {}})


def extract_valves(code: str) -> dict[str, Any]:
    """Retorna o dict VALVES declarado no código (defaults das configurações)."""
    try:
        result = _exec(_EXTRACT, {"code": code})
        return result if isinstance(result, dict) else {}
    except ToolExecutionError:
        return {}
