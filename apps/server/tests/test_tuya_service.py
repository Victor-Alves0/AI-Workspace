"""Testes da descoberta Tuya sem rede nem banco."""
from __future__ import annotations

import pytest

from aiworkspace.integrations import tuya_service


def test_discover_devices_uses_documented_has_more_pagination(monkeypatch):
    calls: list[str] = []

    def fake_request(_conn, _method, path, _body=None):
        calls.append(path)
        if "last_row_key=pagina-2" in path:
            return {
                "success": True,
                "result": {
                    "devices": [
                        {"id": "lamp-2", "name": "Luz corredor", "uid": "user-1"},
                    ],
                    "has_more": False,
                },
            }
        return {
            "success": True,
            "result": {
                "devices": [
                    {"id": "lamp-1", "name": "Luz quarto", "uid": "user-1"},
                ],
                "has_more": True,
                "last_row_key": "pagina-2",
            },
        }

    monkeypatch.setattr(tuya_service, "_request", fake_request)

    devices, uids = tuya_service._discover_devices({})

    assert [device["id"] for device in devices] == ["lamp-1", "lamp-2"]
    assert uids == {"user-1"}
    assert calls == [
        "/v1.0/iot-01/associated-users/devices?size=20",
        "/v1.0/iot-01/associated-users/devices?size=20&last_row_key=pagina-2",
    ]


def test_discover_devices_falls_back_to_current_project_endpoint(monkeypatch):
    calls: list[str] = []

    def fake_request(_conn, _method, path, _body=None):
        calls.append(path)
        if path.startswith("/v1.0/iot-01/associated-users/devices"):
            # Situação observada em alguns projetos: a conta mostra dispositivos,
            # mas o pacote antigo não os entrega neste endpoint.
            return {"success": True, "result": {"devices": [], "total": 5}}
        return {
            "success": True,
            "result": [
                {
                    "id": "iru-1",
                    "customName": "IRU",
                    "category": "wnykq",
                    "isOnline": True,
                },
            ],
        }

    monkeypatch.setattr(tuya_service, "_request", fake_request)

    devices, uids = tuya_service._discover_devices({})

    assert devices == [
        {"id": "iru-1", "name": "IRU", "category": "wnykq", "online": True},
    ]
    assert uids == set()
    assert calls[-1] == "/v2.0/cloud/thing/device?page_size=20"


def test_discover_devices_surfaces_tuya_errors(monkeypatch):
    def fake_request(_conn, _method, path, _body=None):
        if path.startswith("/v1.0/"):
            return {"success": False, "code": 1106, "msg": "permission deny"}
        return {"success": False, "code": 2009, "msg": "project not authorized"}

    monkeypatch.setattr(tuya_service, "_request", fake_request)

    with pytest.raises(RuntimeError) as error:
        tuya_service._discover_devices({})

    message = str(error.value)
    assert "permission deny" in message
    assert "project not authorized" in message


def test_discover_devices_does_not_mask_fallback_error_as_empty(monkeypatch):
    def fake_request(_conn, _method, path, _body=None):
        if path.startswith("/v1.0/"):
            return {"success": True, "result": {"devices": []}}
        return {"success": False, "code": 2009, "msg": "IoT Core não autorizado"}

    monkeypatch.setattr(tuya_service, "_request", fake_request)

    with pytest.raises(RuntimeError, match="IoT Core não autorizado"):
        tuya_service._discover_devices({})
