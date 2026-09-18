"""Regressões de escopo para mídia privada e links públicos revogáveis."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from aiworkspace import image_routes, uploads_service
from aiworkspace.knowledge import links as knowledge_links
from aiworkspace.models import Chat, GeneratedImage
from aiworkspace.providers import image_gen
from aiworkspace.share_routes import _shared_media_urls


def test_shared_tokens_are_resource_and_share_bound():
    resource = str(uuid.uuid4())
    other_resource = str(uuid.uuid4())
    public_id = "share-ativo"

    image_url = image_gen.sign_shared_image_url(resource, public_id)
    image_token = image_url.split("?s=", 1)[1]
    assert image_gen.verify_shared_image_token(resource, image_token) == public_id
    assert image_gen.verify_shared_image_token(other_resource, image_token) is None

    upload_url = uploads_service.sign_shared_url(resource, public_id)
    upload_token = upload_url.split("?s=", 1)[1]
    assert uploads_service.verify_shared_token(resource, upload_token) == public_id
    assert uploads_service.verify_shared_token(other_resource, upload_token) is None

    doc_url = knowledge_links.sign_shared_doc_url(resource, public_id)
    doc_token = doc_url.split("?s=", 1)[1]
    assert knowledge_links.verify_shared_doc_token(resource, doc_token) == public_id
    assert knowledge_links.verify_shared_doc_token(other_resource, doc_token) is None


def test_public_share_replaces_private_media_capabilities():
    image_id, upload_id, doc_id = (str(uuid.uuid4()) for _ in range(3))
    original = (
        f"![imagem](/images/{image_id}?t=privado) "
        f"[anexo](/uploads/{upload_id}?t=privado) "
        f"[fonte](/knowledge/docs/{doc_id}/raw?t=privado)"
    )
    shared = _shared_media_urls(original, "share-atual")
    assert "?t=privado" not in shared
    assert image_gen.verify_shared_image_token(image_id, shared.split("/images/", 1)[1].split("?s=", 1)[1].split(")", 1)[0]) == "share-atual"
    assert "/uploads/" in shared and "/knowledge/docs/" in shared


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/images/x", "headers": []})


def test_private_generated_image_requires_its_owner_session():
    image_id, owner_id, outsider_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    row = SimpleNamespace(id=image_id, user_id=owner_id, chat_id=None, data=b"png", mime="image/png")

    class Db:
        async def get(self, cls, ident):
            return row if cls is GeneratedImage and ident == image_id else None

    async def exercise():
        token = image_gen.sign_image_url(str(image_id)).split("?t=", 1)[1]
        response = await image_routes.get_image(
            image_id, _request(), t=token, user=SimpleNamespace(id=owner_id), db=Db()
        )
        assert response.status_code == 200
        with pytest.raises(HTTPException) as exc:
            await image_routes.get_image(
                image_id, _request(), t=token, user=SimpleNamespace(id=outsider_id), db=Db()
            )
        assert exc.value.status_code == 404

    asyncio.run(exercise())


def test_shared_generated_image_stops_when_chat_is_not_shared():
    image_id, owner_id, chat_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    row = SimpleNamespace(id=image_id, user_id=owner_id, chat_id=chat_id, data=b"png", mime="image/png")
    chat = SimpleNamespace(user_id=owner_id, public_id=None, public_expires_at=None)

    class Db:
        async def get(self, cls, ident):
            if cls is GeneratedImage:
                return row
            if cls is Chat:
                return chat
            return None

    async def exercise():
        token = image_gen.sign_shared_image_url(str(image_id), "old-share").split("?s=", 1)[1]
        with pytest.raises(HTTPException) as exc:
            await image_routes.get_image(image_id, _request(), s=token, user=None, db=Db())
        assert exc.value.status_code == 404

    asyncio.run(exercise())
