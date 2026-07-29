"""Geração de imagens — GenImage Router.

Dois provedores:
  - "openrouter": modelos de imagem do OpenRouter (ex.: google/gemini-2.5-flash-image).
    A imagem volta como data URL em `choices[0].message.images[*].image_url.url`.
  - "openai_compat": endpoint /images/generations compatível com OpenAI (DALL-E, SD
    local), com base_url + chave próprios.

Cada função devolve (bytes, mime). Rodam via httpx (o orchestrator chama em threadpool).
As URLs servidas são assinadas com `app_secret` (pyjwt) — funcionam em <img src> sem
depender de cookie cross-origin.
"""

from __future__ import annotations

import base64
import re
import time
from typing import Any

import httpx
import jwt

from ..config import get_settings
from . import openrouter

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<b64>.+)$", re.DOTALL)


def _decode_data_url(url: str) -> tuple[bytes, str]:
    m = _DATA_URL_RE.match(url.strip())
    if not m:
        raise ValueError("resposta de imagem inesperada (não é data URL base64)")
    return base64.b64decode(m.group("b64")), m.group("mime") or "image/png"


async def generate_openrouter(
    api_key: str, model: str, prompt: str, images: list[str] | None = None
) -> tuple[bytes, str, float]:
    """Gera (ou EDITA) uma imagem por um modelo de imagem do OpenRouter.

    `images`: URLs/data-URLs de imagens de ENTRADA — quando presentes, o modelo edita
    com esse contexto (estilo nano-banana). Retorna (bytes, mime, custo_usd)."""
    settings = get_settings()
    # com imagens de entrada, o content vira multipart (texto + image_url); sem elas,
    # uma string simples (compat com modelos que não aceitam array de 1 item).
    if images:
        content: Any = [
            {"type": "text", "text": prompt},
            *[{"type": "image_url", "image_url": {"url": u}} for u in images if u],
        ]
    else:
        content = prompt
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "modalities": ["image", "text"],
    }
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(
            f"{settings.openrouter_base_url}/chat/completions",
            headers=openrouter._headers(api_key),
            json=payload,
        )
    if r.status_code != 200:
        raise RuntimeError(f"OpenRouter image gen falhou ({r.status_code}): {r.text[:300]}")
    data = r.json()
    msg = ((data.get("choices") or [{}])[0].get("message")) or {}
    out_images = msg.get("images") or []
    if not out_images:
        # alguns modelos devolvem a imagem como parte do content
        raise RuntimeError("o modelo não retornou imagem (verifique se é um modelo de imagem)")
    url = (out_images[0].get("image_url") or {}).get("url") or ""
    # custo real reportado pelo OpenRouter (para entrar no gasto do turno)
    usage = data.get("usage") or {}
    cost = usage.get("cost")
    if cost is None and isinstance(usage.get("cost_details"), dict):
        cost = usage["cost_details"].get("upstream_inference_cost")
    b, mime = _decode_data_url(url)
    return b, mime, float(cost) if isinstance(cost, (int, float)) else 0.0


async def generate_openai_compat(
    base_url: str, api_key: str, model: str, prompt: str, size: str = "1024x1024"
) -> tuple[bytes, str]:
    base = (base_url or "").rstrip("/")
    payload = {"model": model, "prompt": prompt, "n": 1, "size": size, "response_format": "b64_json"}
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(
            f"{base}/images/generations",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
    if r.status_code != 200:
        raise RuntimeError(f"provedor de imagem falhou ({r.status_code}): {r.text[:300]}")
    data = r.json()
    item = (data.get("data") or [{}])[0]
    b64 = item.get("b64_json")
    if b64:
        return base64.b64decode(b64), "image/png"
    url = item.get("url")
    if url:  # modo url: baixa os bytes
        async with httpx.AsyncClient(timeout=60) as client:
            img = await client.get(url)
        img.raise_for_status()
        return img.content, img.headers.get("content-type", "image/png")
    raise RuntimeError("resposta de imagem vazia")


async def generate(
    cfg: dict[str, Any], keys: dict[str, str], prompt: str,
    size: str = "1024x1024", images: list[str] | None = None,
) -> tuple[bytes, str, float]:
    """Gera (ou edita) a imagem pelo provedor configurado no GenImage Router.

    cfg: {provider, model, base_url?}. keys: {openrouter?, imagegen?}.
    `images`: imagens de entrada p/ EDIÇÃO (só o provedor openrouter usa hoje).
    Retorna (bytes, mime, custo_usd).
    """
    provider = (cfg.get("provider") or "openrouter").strip()
    model = (cfg.get("model") or "").strip()
    if not model:
        raise RuntimeError("nenhum modelo de imagem configurado no GenImage Router")
    if provider == "openai_compat":
        key = keys.get("imagegen") or ""
        if not key:
            raise RuntimeError("configure a chave do provedor de imagem em Conexões → APIs")
        # /images/generations não faz edição por contexto; ignora `images` por ora.
        b, mime = await generate_openai_compat(cfg.get("base_url") or "", key, model, prompt, size)
        return b, mime, 0.0
    # padrão: openrouter (suporta edição com imagens de entrada)
    key = keys.get("openrouter") or ""
    if not key:
        raise RuntimeError("configure sua chave do OpenRouter")
    return await generate_openrouter(key, model, prompt, images)


# --------------------------------------------------------------------------- #
# URL assinada p/ servir a imagem em <img src> (sem cookie cross-origin)
# --------------------------------------------------------------------------- #
def sign_image_url(image_id: str, ttl_days: int = 3650) -> str:
    """Caminho relativo `/images/<id>?t=<sig>`. O token só codifica o id (URL-capacidade)."""
    now = int(time.time())
    tok = jwt.encode(
        {"img": image_id, "iat": now, "exp": now + ttl_days * 86400},
        get_settings().app_secret,
        algorithm="HS256",
    )
    return f"/images/{image_id}?t={tok}"


def verify_image_token(image_id: str, token: str) -> bool:
    try:
        data = jwt.decode(token, get_settings().app_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return False
    return data.get("img") == image_id
