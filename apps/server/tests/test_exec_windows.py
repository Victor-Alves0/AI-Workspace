"""Exec do Codespace no Windows: Job Object e o shell certo.

No Linux o sandbox tem RLIMIT_CPU e mata o grupo de processos. No Windows não havia
teto nenhum, e o "mata a árvore" era um `taskkill /T` que perde netos cujo pai já
saiu. Agora: Job Object (teto de CPU + a árvore inteira morre junto) e, havendo Git
for Windows, o bash dele — os prompts ensinam bash. Só roda no Windows.
"""
from __future__ import annotations

import subprocess
import sys
import time

import pytest

from aiworkspace.codespace import exec_service
from aiworkspace.config import get_settings

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="comportamento específico do Windows")

PY = sys.executable.replace("\\", "/")


def _python_vivo(marca: str) -> int:
    saida = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         f"(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         f"Where-Object {{ $_.CommandLine -match '{marca}' }}).Count"],
        capture_output=True, text=True, timeout=60,
    ).stdout.strip()
    return int(saida or 0)


@pytest.mark.skipif(exec_service.windows_bash() is None, reason="sem Git for Windows")
def test_comando_em_sintaxe_bash_funciona(tmp_path):
    (tmp_path / "a.txt").write_text("conteudo", encoding="utf-8")

    r = exec_service.run_command(tmp_path, "ls && cat a.txt | tr a-z A-Z && echo \"$((2+3))\"")

    assert r["exit_code"] == 0, r
    assert "a.txt" in r["output"] and "CONTEUDO" in r["output"] and "5" in r["output"]


def test_segredos_do_servidor_nao_chegam_ao_comando(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_SECRET", "nao-pode-vazar")
    comando = f'"{PY}" -c "import os; print(os.environ.get(\'APP_SECRET\', \'ausente\'))"'

    r = exec_service.run_command(tmp_path, comando)

    assert "ausente" in r["output"] and "nao-pode-vazar" not in r["output"]


def test_teto_de_cpu_encerra_e_explica(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "code_exec_cpu_seconds", 1, raising=False)
    comando = f'"{PY}" -c "while True: pass"'

    r = exec_service.run_command(tmp_path, comando, timeout=60)

    assert not r["timed_out"]
    assert "teto de CPU" in r["output"]


def test_timeout_mata_a_arvore_inclusive_neto(tmp_path):
    """O filho cria um neto e fica esperando; no timeout, o neto também tem de morrer
    — o `taskkill /T` de antes perdia neto de pai que já saiu."""
    marca = f"neto_{int(time.time() * 1000)}"
    script = tmp_path / "pai.py"
    script.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)  # {marca}'])\n"
        "time.sleep(120)\n", encoding="utf-8")

    r = exec_service.run_command(tmp_path, f'"{PY}" "{script.as_posix()}"', timeout=3)

    assert r["timed_out"]
    time.sleep(1)
    assert _python_vivo(marca) == 0


def test_descricao_diz_ao_modelo_o_ambiente_real():
    nota = exec_service.environment_note()

    assert "WINDOWS" in nota
    assert ("Git Bash" in nota) == (exec_service.windows_bash() is not None)
    assert "mise" in nota


def test_nunca_usa_o_bash_do_wsl(monkeypatch, tmp_path):
    """C:\\Windows\\System32\\bash.exe é o do WSL: outro sistema de arquivos, não enxerga
    o projeto. Mesmo forçado por engano, não serve."""
    exec_service.windows_bash.cache_clear()
    try:
        monkeypatch.setenv("AIW_BASH", r"C:\Windows\System32\bash.exe")
        assert exec_service.windows_bash() is None
        monkeypatch.delenv("AIW_BASH")
        exec_service.windows_bash.cache_clear()
        achado = exec_service.windows_bash()
        assert achado is None or "system32" not in achado.lower()
    finally:
        exec_service.windows_bash.cache_clear()
