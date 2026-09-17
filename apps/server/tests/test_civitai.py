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
    assert "call generate directly" in tools["civitai.media.use"]["model_desc"]
    assert "never ask for" in tools["civitai.media.use"]["model_desc"]
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


def test_submit_image_uses_current_v2_workflow_contract(monkeypatch):
    seen = {}

    def fake_request(method, url, headers=None, json=None, params=None, timeout=None):
        seen.update(method=method, url=url, headers=headers, body=json, params=params)
        return _response(202, {"id": "wf_1", "status": "processing", "steps": []})

    monkeypatch.setattr(cv.httpx, "request", fake_request)
    result = cv.submit_image("token", "a moon city", width=1024, height=768,
                             options_json='{"guidanceScale": 4}')
    assert result["id"] == "wf_1"
    assert seen["method"] == "POST"
    assert seen["url"].startswith("https://orchestration-new.civitai.com/")
    assert seen["url"].endswith("/v2/consumer/workflows")
    assert seen["headers"]["Authorization"] == "Bearer token"
    step = seen["body"]["steps"][0]
    assert step["$type"] == "textToImage"
    assert "engine" not in step["input"]
    assert step["input"]["guidanceScale"] == 4
    assert seen["params"]["hideMatureContent"] == "true"


def test_submit_image_enables_mature_output_and_resolves_model_url(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, params=None, timeout=None, follow_redirects=None):
        assert url.endswith("/model-versions/3239376")
        return _response(200, {
            "id": 3239376, "modelId": 1171727,
            "air": "urn:air:anima:lora:civitai:1171727@3239376",
            "model": {"type": "LORA"},
        })

    def fake_request(method, url, headers=None, json=None, params=None, timeout=None):
        seen.update(url=url, body=json, params=params)
        return _response(202, {"id": "wf_1", "status": "processing", "steps": []})

    monkeypatch.setattr(cv.httpx, "get", fake_get)
    monkeypatch.setattr(cv.httpx, "request", fake_request)
    result = cv.submit_image(
        "token", "an anime scene", model="https://civitai.com/models/1171727?modelVersionId=3239376",
        mature=True,
    )

    assert result["id"] == "wf_1"
    assert seen["body"]["allowMatureContent"] is True
    assert seen["params"]["hideMatureContent"] == "false"
    assert seen["body"]["steps"][0]["input"]["additionalNetworks"] == {
        "urn:air:anima:lora:civitai:1171727@3239376": {"type": "Lora", "strength": 1.0}
    }


def test_model_query_uses_cursor_instead_of_incompatible_page(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, params=None, timeout=None, follow_redirects=None):
        seen["params"] = params
        return _response(200, {"items": [], "metadata": {"nextCursor": "next"}})

    monkeypatch.setattr(cv.httpx, "get", fake_get)
    cv.search_models("", "flux", page=9, cursor="opaque", limit=3)
    assert seen["params"]["cursor"] == "opaque"
    assert "page" not in seen["params"]


def test_catalog_results_are_compact_and_bounded_for_agent_context(monkeypatch):
    seen = {}
    giant_trigger = "trigger " * 5_000
    payload = {
        "items": [
            {
                "id": index,
                "name": f"Model {index}",
                "type": "LORA",
                "creator": {"username": "creator"},
                "tags": ["unused"] * 50,
                "stats": {"downloadCount": 999},
                "modelVersions": [
                    {
                        "id": index * 100 + version,
                        "name": f"Version {version}",
                        "baseModel": "Flux",
                        "trainedWords": [giant_trigger] * 20,
                        "downloadUrl": "https://civitai.example/very-long-download-url",
                    }
                    for version in range(8)
                ],
            }
            for index in range(10)
        ],
        "metadata": {"nextCursor": "cursor-2", "nextPage": "very-long-url"},
    }

    def fake_get(url, headers=None, params=None, timeout=None, follow_redirects=None):
        seen["params"] = params
        return _response(200, payload)

    monkeypatch.setattr(cv.httpx, "get", fake_get)
    result = cv.search_models("", "flux", limit=50)
    encoded = json.dumps(result, ensure_ascii=False)

    assert seen["params"]["limit"] == 4
    assert len(result["items"]) == 4
    assert all(len(item["versions"]) == 2 for item in result["items"])
    assert "trained_words" not in encoded
    assert "download_url" not in encoded
    assert "nextPage" not in encoded
    assert len(encoded) < 5_000


def test_gallery_results_are_compact_and_bounded_for_agent_context(monkeypatch):
    seen = {}
    giant_prompt = "cinematic prompt " * 5_000
    payload = {
        "items": [
            {
                "id": index,
                "url": "https://image.civitai.example/" + ("x" * 2_000),
                "width": 1024,
                "height": 1024,
                "nsfwLevel": 1,
                "username": "creator",
                "stats": {"likeCount": 999_999},
                "meta": {"prompt": giant_prompt, "negativePrompt": giant_prompt,
                         "Model": "Flux", "Sampler": "Euler", "steps": 20},
            }
            for index in range(10)
        ],
        "metadata": {"nextCursor": "cursor-2", "nextPage": "very-long-url"},
    }

    def fake_get(url, headers=None, params=None, timeout=None, follow_redirects=None):
        seen["params"] = params
        return _response(200, payload)

    monkeypatch.setattr(cv.httpx, "get", fake_get)
    result = cv.search_images("", limit=50)
    encoded = json.dumps(result, ensure_ascii=False)

    assert seen["params"]["limit"] == 4
    assert len(result["items"]) == 4
    assert "https://image.civitai.example" not in encoded
    assert "cinematic prompt" not in encoded
    assert "likeCount" not in encoded
    assert "nextPage" not in encoded
    assert len(encoded) < 3_000


def test_model_version_returns_only_generation_fields(monkeypatch):
    giant = "metadata " * 5_000
    payload = {
        "id": 22,
        "name": "Flux detail",
        "baseModel": "Flux",
        "air": "urn:air:flux:22",
        "trainedWords": [giant] * 10,
        "description": giant,
        "files": [{"downloadUrl": "https://download.example/" + giant}],
        "images": [{"url": "https://image.example/" + giant}],
    }

    def fake_get(url, headers=None, params=None, timeout=None, follow_redirects=None):
        return _response(200, payload)

    monkeypatch.setattr(cv.httpx, "get", fake_get)
    result = cv.get_model_version("", 22)
    encoded = json.dumps(result, ensure_ascii=False)

    assert result["air"] == "urn:air:flux:22"
    assert len(result["trained_words"]) == 6
    assert "download.example" not in encoded
    assert "image.example" not in encoded
    assert len(encoded) < 2_500


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


def test_connected_civitai_discovery_tells_model_to_call_not_request_a_token():
    """A saved token is deliberately invisible, but its ready state must be visible.

    Otherwise the model sees the generic API requirement and asks the person for a
    second token instead of executing the tool already enabled for this chat.
    """
    token = "a-secret-that-must-never-appear-in-tool-metadata"
    sift = _make_sift(token)
    raw = sift.dispatch("search_tools", {"query": "generate image", "domain": "civitai"})
    result = raw if isinstance(raw, str) else json.dumps(raw)

    assert "Civitai is connected for this chat" in result
    assert "call this tool immediately" in result
    assert token not in result


def test_disconnected_civitai_discovery_defers_connection_prompt_until_tool_error():
    sift = _make_sift()
    raw = sift.dispatch("search_tools", {"query": "generate image", "domain": "civitai"})
    result = raw if isinstance(raw, str) else json.dumps(raw)

    assert "Civitai is not connected for this chat" in result
    assert "only if generate/estimate returns a connection error" in result


def test_unknown_action_lists_recovery_path():
    result = _dispatch(_make_sift(), {"action": "unknown"})
    assert "search_models" in result["error"] and "generate" in result["error"]
