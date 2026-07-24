"""Higgsfield: cliente da API (submit/poll/teste de credencial) e o gating da
tool na SIFT. Puro/hermético — o httpx é falsificado por monkeypatch; nada de
rede, DB ou credencial real. Também cobre a categoria de origem das ferramentas
(Nativo / Codespace / Integração) que a UI usa p/ ícone + hover."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from aiworkspace.integrations import higgsfield_service as hf
from aiworkspace.tools import sift_service


# --------------------------------------------------------------------------- #
# tool_category — a regra de origem (por prefixo, nunca flag à mão)
# --------------------------------------------------------------------------- #
def test_tool_category_prefix_rules():
    assert sift_service.tool_category("utils.time.now") == {"category": "native"}
    assert sift_service.tool_category("web.browser.use") == {"category": "native"}
    assert sift_service.tool_category("code.graph.query") == {"category": "codespace"}
    assert sift_service.tool_category("google.gmail.mailbox") == {
        "category": "integration", "integration": "Google"}
    assert sift_service.tool_category("smartlife.tuya.devices") == {
        "category": "integration", "integration": "Tuya Smart Life"}
    assert sift_service.tool_category("higgsfield.media.generate") == {
        "category": "integration", "integration": "Higgsfield"}


def test_system_tools_all_carry_a_category():
    """A UI depende do campo em TODA entrada — uma tool nova sem categoria
    apareceria sem ícone/hover de origem."""
    for t in sift_service.system_tools():
        assert t.get("category") in ("native", "codespace", "integration"), t["path"]
        if t["category"] == "integration":
            assert t.get("integration"), t["path"]


def test_higgsfield_is_a_builtin_tool():
    paths = [t["path"] for t in sift_service.system_tools()]
    assert "higgsfield.media.generate" in paths


# --------------------------------------------------------------------------- #
# Cliente da API (httpx falsificado)
# --------------------------------------------------------------------------- #
_CONN = {"api_key": "k", "api_secret": "s"}


def _resp(status_code: int, payload: dict | None = None, text: str = ""):
    return SimpleNamespace(
        status_code=status_code,
        json=lambda: payload if payload is not None else {},
        text=text,
        headers={},
    )


def test_test_connection_distinguishes_auth_from_not_found(monkeypatch):
    """404 num request aleatório = credencial VÁLIDA (só não achou o id);
    401/403 = credencial ruim. É a sonda barata que não gasta créditos."""
    monkeypatch.setattr(hf.httpx, "get", lambda *a, **k: _resp(404))
    assert hf.test_connection(_CONN)["ok"] is True
    monkeypatch.setattr(hf.httpx, "get", lambda *a, **k: _resp(401))
    out = hf.test_connection(_CONN)
    assert out["ok"] is False and "credenciais" in out["error"]


def test_submit_sends_key_auth_and_returns_request_id(monkeypatch):
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"], seen["headers"], seen["json"] = url, headers, json
        return _resp(200, {"request_id": "r1", "status_url": "x", "cancel_url": "y"})

    monkeypatch.setattr(hf.httpx, "post", fake_post)
    out = hf.submit(_CONN, "higgsfield-ai/soul/standard", {"prompt": "p"})
    assert out["request_id"] == "r1"
    assert seen["url"].endswith("/higgsfield-ai/soul/standard")
    assert seen["headers"]["Authorization"] == "Key k:s"  # formato da API oficial
    assert seen["json"] == {"prompt": "p"}


def test_submit_maps_http_errors(monkeypatch):
    monkeypatch.setattr(hf.httpx, "post", lambda *a, **k: _resp(401))
    assert "credenciais" in hf.submit(_CONN, "m", {})["error"]
    monkeypatch.setattr(hf.httpx, "post", lambda *a, **k: _resp(422, text="bad arg"))
    assert "422" in hf.submit(_CONN, "m", {})["error"]


def test_wait_polls_until_terminal_status(monkeypatch):
    seq = iter([{"status": "queued"}, {"status": "in_progress"},
                {"status": "completed", "images": [{"url": "u"}]}])
    monkeypatch.setattr(hf, "fetch_status", lambda conn, rid: next(seq))
    monkeypatch.setattr(hf.time, "sleep", lambda s: None)
    out = hf.wait(_CONN, "r1", budget_s=60)
    assert out["status"] == "completed"


def test_wait_gives_up_at_budget_and_returns_last_status(monkeypatch):
    monkeypatch.setattr(hf, "fetch_status", lambda conn, rid: {"status": "in_progress"})
    monkeypatch.setattr(hf.time, "sleep", lambda s: None)
    clock = iter(range(0, 10_000, 5))
    monkeypatch.setattr(hf.time, "monotonic", lambda: float(next(clock)))
    out = hf.wait(_CONN, "r1", budget_s=20)
    assert out["status"] == "in_progress"  # quem chama devolve o request_id p/ retomar


# --------------------------------------------------------------------------- #
# Tool na SIFT — roteamento e gating (sem rede: só ações que não chamam a API)
# --------------------------------------------------------------------------- #
def _make_sift(cfg):
    from sift import Sift
    s = Sift()
    sift_service._register_builtins(
        s, sift_service.SearchConfig(), {"higgsfield.media.generate"},
        higgsfield_cfg=cfg,
    )
    s.build_index()
    return s


def _dispatch(s, params):
    return s.dispatch("execute_tool", {"path": "higgsfield.media.generate", "params": params})


def _loads(raw):
    import json
    return json.loads(raw) if isinstance(raw, str) else raw


def test_models_action_works_without_connection():
    """O catálogo é informativo — não exige credencial (e é a resposta certa
    quando o usuário pergunta 'o que dá pra gerar?')."""
    out = _loads(_dispatch(_make_sift(None), {"action": "models"}))
    ids = [m["id"] for m in out["models"]["image"]] + [m["id"] for m in out["models"]["video"]]
    assert hf.DEFAULT_IMAGE_MODEL in ids and hf.DEFAULT_VIDEO_MODEL in ids


def test_generate_without_connection_says_how_to_connect():
    out = _loads(_dispatch(_make_sift(None), {"action": "image", "prompt": "x"}))
    assert "not connected" in out["error"]


@pytest.fixture()
def connected_sift():
    return _make_sift(sift_service.HiggsfieldConfig(conn=dict(_CONN)))


def test_unknown_model_is_rejected_with_recovery_hint(connected_sift):
    out = _loads(_dispatch(connected_sift, {"action": "image", "prompt": "x",
                                            "model": "nao/existe"}))
    assert "unknown image model" in out["error"] and "models" in out["error"]


def test_video_requires_image_url(connected_sift):
    out = _loads(_dispatch(connected_sift, {"action": "video", "prompt": "x"}))
    assert "image_url" in out["error"]


def test_status_requires_request_id(connected_sift):
    out = _loads(_dispatch(connected_sift, {"action": "status"}))
    assert "request_id" in out["error"]


def test_unknown_action_lists_the_valid_ones(connected_sift):
    out = _loads(_dispatch(connected_sift, {"action": "xpto"}))
    assert "image/video/models/status" in out["error"]


def test_image_generation_happy_path(monkeypatch, connected_sift):
    """Fluxo completo com API + storage falsificados: submit → wait → download
    → guarda → card {kind:'image', url assinada}."""
    monkeypatch.setattr(hf, "submit", lambda conn, m, a: {"request_id": "r9"})
    monkeypatch.setattr(hf, "wait", lambda conn, rid, budget_s: {
        "status": "completed", "images": [{"url": "https://cdn.example/img.png"}]})
    monkeypatch.setattr(hf, "download", lambda url: (b"png-bytes", "image/png"))

    stored = {}

    async def fake_store(user_id, chat_id, data, mime="", prompt="", model=""):
        stored.update(mime=mime, model=model, size=len(data))
        return "11111111-1111-1111-1111-111111111111"

    monkeypatch.setattr(sift_service, "_store_media", fake_store)
    out = _loads(_dispatch(connected_sift, {"action": "image", "prompt": "um lago"}))
    assert out["kind"] == "image" and "/images/" in out["url"]
    assert stored["mime"] == "image/png" and stored["model"] == hf.DEFAULT_IMAGE_MODEL


def test_video_result_trusts_real_mime_over_json_key(monkeypatch, connected_sift):
    """A resposta pode vir como images[] mesmo sendo vídeo em modelos novos —
    o mime do download decide o card."""
    monkeypatch.setattr(hf, "submit", lambda conn, m, a: {"request_id": "r9"})
    monkeypatch.setattr(hf, "wait", lambda conn, rid, budget_s: {
        "status": "completed", "video": {"url": "https://cdn.example/v.mp4"}})
    monkeypatch.setattr(hf, "download", lambda url: (b"mp4", "video/mp4"))

    async def fake_store(*a, **k):
        return "22222222-2222-2222-2222-222222222222"

    monkeypatch.setattr(sift_service, "_store_media", fake_store)
    out = _loads(_dispatch(connected_sift, {
        "action": "video", "prompt": "anima", "image_url": "https://example.com/a.jpg"}))
    assert out["kind"] == "video"


def test_timeout_returns_request_id_to_resume(monkeypatch, connected_sift):
    monkeypatch.setattr(hf, "submit", lambda conn, m, a: {"request_id": "r77"})
    monkeypatch.setattr(hf, "wait", lambda conn, rid, budget_s: {"status": "in_progress"})
    out = _loads(_dispatch(connected_sift, {"action": "image", "prompt": "x"}))
    assert out["request_id"] == "r77" and "status" in out


def test_private_image_url_is_rejected(monkeypatch, connected_sift):
    """Anti-SSRF: a referência de vídeo não pode apontar p/ host interno."""
    out = _loads(_dispatch(connected_sift, {
        "action": "video", "prompt": "x", "image_url": "http://192.168.1.199:8000/x.png"}))
    assert "public" in out["error"]
