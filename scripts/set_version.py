#!/usr/bin/env python3
"""Define a versão do produto em TODOS os arquivos, a partir da fonte única.

A versão vive em `apps/server/pyproject.toml` ([project].version). O backend a lê dali
(`aiworkspace.__version__`), mas os arquivos do desktop precisam do valor LITERAL — eles
não conseguem ler outro arquivo em tempo de build. Este script propaga.

    python scripts/set_version.py 0.5.1     # define e propaga
    python scripts/set_version.py --check   # só verifica (usado pelo CI/teste)

Motivo de existir: a versão estava em 5 lugares e três subiram para 0.5.0 enquanto o
backend e o pyproject ficaram em 0.1.0 — a checagem de atualização passou a dizer "há
atualização disponível" para sempre. Ver tests/test_version_sync.py.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (caminho, regex com um grupo capturando a versão) — o grupo 1 é substituído
TARGETS: list[tuple[Path, str]] = [
    (ROOT / "apps/server/pyproject.toml", r'(?m)^version\s*=\s*"([^"]+)"'),
    (ROOT / "desktop/package.json", r'"version"\s*:\s*"([^"]+)"'),
    (ROOT / "desktop/src-tauri/tauri.conf.json", r'"version"\s*:\s*"([^"]+)"'),
    (ROOT / "desktop/src-tauri/Cargo.toml", r'(?m)^version\s*=\s*"([^"]+)"'),
]

SOURCE = TARGETS[0]


def current(path: Path, pattern: str) -> str | None:
    try:
        m = re.search(pattern, path.read_text(encoding="utf-8"))
    except OSError:
        return None
    return m.group(1) if m else None


def source_version() -> str:
    v = current(*SOURCE)
    if not v:
        raise SystemExit(f"não achei a versão em {SOURCE[0]}")
    return v


def check() -> int:
    want = source_version()
    bad = []
    for path, pattern in TARGETS[1:]:
        got = current(path, pattern)
        if got != want:
            bad.append(f"  {path.relative_to(ROOT).as_posix()}: {got!r} (esperado {want!r})")
    if bad:
        print(f"versões DIVERGENTES (fonte: {want}):", *bad, sep="\n")
        print("\nCorrija com: python scripts/set_version.py " + want)
        return 1
    print(f"todas as versões em sincronia: {want}")
    return 0


def apply(new: str) -> int:
    if not re.fullmatch(r"\d+\.\d+\.\d+", new):
        raise SystemExit(f"versão inválida: {new!r} (use X.Y.Z)")
    for path, pattern in TARGETS:
        text = path.read_text(encoding="utf-8")
        m = re.search(pattern, text)
        if not m:
            raise SystemExit(f"não achei o campo de versão em {path}")
        # substitui só o GRUPO (não a linha inteira): preserva formatação
        start, end = m.span(1)
        path.write_text(text[:start] + new + text[end:], encoding="utf-8")
        print(f"  {path.relative_to(ROOT).as_posix()} -> {new}")
    print(f"\nversão definida: {new}\nLembre da tag do release: git tag v{new}")
    return 0


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "--check"
    sys.exit(check() if arg in ("--check", "-c") else apply(arg.lstrip("v")))
