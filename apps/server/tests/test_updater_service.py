"""Updater (infra/updater): o único container com o socket do Docker. Aqui o
serviço roda de verdade (HTTP na loopback) com um script falso no lugar do update."""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[3] / "infra" / "updater" / "updater.py"


def _carregar(tmp_path, monkeypatch, script: str):
    monkeypatch.setenv("UPDATER_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("UPDATER_SCRIPT", script)
    spec = importlib.util.spec_from_file_location(f"updater_{tmp_path.name}", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def servico(tmp_path, monkeypatch):
    def subir(script_body: str):
        script = tmp_path / ("job.cmd" if sys.platform == "win32" else "job.sh")
        script.write_text(script_body, encoding="utf-8")
        script.chmod(0o755)
        mod = _carregar(tmp_path, monkeypatch, str(script))
        srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return mod, f"http://127.0.0.1:{srv.server_address[1]}", srv
    return subir


def _req(url, method="GET", token=None):
    r = urllib.request.Request(url, method=method,
                               headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _script(sucesso: bool) -> str:
    if sys.platform == "win32":
        return "@echo off\r\nping -n 2 127.0.0.1 >nul\r\n" + ("exit /b 0\r\n" if sucesso else "echo quebrou\r\nexit /b 3\r\n")
    return "#!/bin/sh\nsleep 1\n" + ("exit 0\n" if sucesso else "echo quebrou; exit 3\n")


def _espera(url, token, estado):
    for _ in range(50):
        code, st = _req(url + "/status", token=token)
        if st.get("state") == estado:
            return st
        time.sleep(0.2)
    raise AssertionError(f"status não virou {estado}: {st}")


def test_sem_token_nada_acontece(servico):
    mod, url, srv = servico(_script(True))
    try:
        assert _req(url + "/update", "POST")[0] == 401
        assert _req(url + "/update", "POST", token="errado")[0] == 401
        assert _req(url + "/status")[0] == 401
        assert _req(url + "/health")[0] == 200
    finally:
        srv.shutdown()


def test_pedido_roda_uma_vez_e_recusa_duplicado(servico):
    mod, url, srv = servico(_script(True))
    try:
        assert _req(url + "/update", "POST", token=mod.TOKEN)[0] == 202
        assert _req(url + "/update", "POST", token=mod.TOKEN)[0] == 409   # já rodando
        assert _req(url + "/status", token=mod.TOKEN)[1]["state"] == "running"
    finally:
        srv.shutdown()


def test_falha_do_script_vira_failed_com_log(servico):
    mod, url, srv = servico(_script(False))
    try:
        _req(url + "/update", "POST", token=mod.TOKEN)
        st = _espera(url, mod.TOKEN, "failed")
        assert "quebrou" in st["log"]
    finally:
        srv.shutdown()


def test_reinicio_no_meio_marca_interrompida(tmp_path, monkeypatch):
    (tmp_path / "status.json").write_text(json.dumps({"state": "running"}))
    mod = _carregar(tmp_path, monkeypatch, "nada")
    assert mod._read_status()["state"] == "failed"
    # o token é gerado uma vez e reaproveitado (o servidor lê o mesmo arquivo)
    assert (tmp_path / "token").read_text().strip() == mod.TOKEN
    assert _carregar(tmp_path, monkeypatch, "nada").TOKEN == mod.TOKEN
