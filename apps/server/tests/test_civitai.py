"""Civitai: cliente oficial, tool SIFT unificada e categorização."""

from __future__ import annotations

import json
from types import SimpleNamespace

from aiworkspace.integrations import civitai_service as cv
from aiworkspace.tools import sift_service


def _response(status: int, payload: dict | None = None, text: str = ""):
    return SimpleNamespace(
        status_code=status,
        json=lambda: payload if payload is not None else {},
        text=text,
    )


def test_civitai_is_one_builtin_integration_tool():
    tools = {tool["path"]: tool for tool in sift_service.system_tools()}
    assert "civitai.media.use" in tools
    assert sift_service.tool_category("civitai.media.use") == {
        "category": "integration", "integration": "Civitai",
    }
    assert not any(path.startswith("civitai.") and path != "civitai.media.use" for path in tools)


def test_connection_uses_bearer_token(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen.update(url=url, headers=headers, timeout=timeout)
        return _response(200, {"username": "victor", "tier": "free"})

    monkeypatch.setattr(cv.httpx, "get", fake_get)
    result = cv.test_connection("secret-token")
    assert result == {"ok": True, "user": "victor", "tier": "free"}
    assert seen["url"].endswith("/api/v1/me")
    assert seen["headers"]["Authorization"] == "Bearer secret-token"


def test_submit_image_uses_v2_workflow_contract(monkeypatch):
    seen = {}

    def fake_request(method, url, headers=None, json=None, params=None, timeout=None):
        seen.update(method=method, url=url, headers=headers, body=json, params=params)
        return _response(202, {"id": "wf_1", "status": "processing", "steps": []})

    monkeypatch.setattr(cv.httpx, "request", fake_request)
    result = cv.submit_image(
        "token", "a moon city", engine="flux", width=1024, height=768,
        options_json='{"guidanceScale": 4}',
    )
    assert result["id"] == "wf_1"
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/v2/consumer/workflows")
    assert seen["headers"]["Authorization"] == "Bearer token"
    step = seen["body"]["steps"][0]
    assert step["$type"] == "imageGen"
    assert step["input"]["engine"] == "flux"
    assert step["input"]["guidanceScale"] == 4
    assert seen["params"]["hideMatureContent"] == "true"


def test_model_query_uses_cursor_instead_of_incompatible_page(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, params=None, timeout=None, follow_redirects=None):
        seen["params"] = params
        return _response(200, {"items": [], "metadata": {"nextCursor": "next"}})

    monkeypatch.setattr(cv.httpx, "get", fake_get)
    cv.search_models("", "flux", page=9, cursor="opaque", limit=3)
    assert seen["params"]["cursor"] == "opaque"
    assert "page" not in seen["params"]


def test_workflow_outputs_accept_images_and_blobs():
    outputs = cv.workflow_outputs({"steps": [{"output": {
        "images": [{"url": "https://x/image.png"}],
        "blobs": [{"url": "https://x/other.webp", "contentType": "image/webp"}],
    }}]})
    assert [item["url"] for item in outputs] == [
        "https://x/image.png", "https://x/other.webp",
    ]


def _make_sift(token: str = ""):
    from sift import Sift

    sift = Sift()
    sift_service._register_builtins(
        sift, sift_service.SearchConfig(), {"civitai.media.use"},
        civitai_cfg=sift_service.CivitaiConfig(conn={"token": token}),
    )
    sift.build_index()
    return sift


def _dispatch(sift, params):
    raw = sift.dispatch("execute_tool", {"path": "civitai.media.use", "params": params})
    return json.loads(raw) if isinstance(raw, str) else raw


def test_public_catalog_action_works_without_connection(monkeypatch):
    monkeypatch.setattr(cv, "search_models", lambda *args, **kwargs: {
        "items": [{"id": 1, "name": "Model"}], "metadata": {},
    })
    result = _dispatch(_make_sift(), {"action": "search_models", "query": "model"})
    assert result["items"][0]["id"] == 1


def test_generation_requires_connection():
    result = _dispatch(_make_sift(), {"action": "generate", "prompt": "a castle"})
    assert "not connected" in result["error"]


def test_unknown_action_lists_recovery_path():
    result = _dispatch(_make_sift(), {"action": "unknown"})
    assert "search_models" in result["error"] and "generate" in result["error"]
