"""PageWatcher — dispara quando o conteúdo de uma página web muda.

config: {
  "url": "https://…",          # obrigatório
  "contains": "texto"          # opcional: só considera "mudou" se este texto
                                #   aparecer/sumir (útil p/ "quando abrir inscrições")
}
Extrai o texto (sem HTML), compara por hash com o estado anterior. O primeiro
disparo estabelece a linha de base (não notifica).
"""

from __future__ import annotations

import hashlib

import httpx

from ...deep_search import _html_to_text, _is_public_url

_UA = "Mozilla/5.0 (compatible; AIWorkspace-Watcher/1.0)"
_MAX = 8000


async def check(config: dict, state: dict, deps) -> dict:
    url = (config.get("url") or "").strip()
    if not url:
        return {"changed": False, "new_state": state, "error": "informe a URL da página"}
    if not _is_public_url(url):
        return {"changed": False, "new_state": state, "error": "URL inválida ou não pública"}

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": _UA}, timeout=10)
        if r.status_code != 200:
            return {"changed": False, "new_state": state, "error": f"HTTP {r.status_code}"}
        text = _html_to_text(r.text)[:_MAX]
    except Exception as exc:  # noqa: BLE001
        return {"changed": False, "new_state": state, "error": f"falha ao buscar a página: {exc}"}

    digest = hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()
    contains = (config.get("contains") or "").strip().lower()
    present = contains in text.lower() if contains else None

    prev_hash = state.get("hash")
    prev_present = state.get("present")
    new_state = {"hash": digest, "present": present}

    # primeira observação: só estabelece a linha de base
    if not prev_hash:
        return {"changed": False, "new_state": new_state}

    if contains:
        # só dispara quando a presença do texto ALTERNA (apareceu ou sumiu)
        changed = present != prev_present
        status = "apareceu" if present else "sumiu"
        summary = f'A página {url} mudou: o texto "{contains}" {status}.'
    else:
        changed = digest != prev_hash
        summary = f"A página {url} mudou.\n\nTrecho atual:\n{text[:1500]}"

    return {"changed": changed, "summary_input": summary if changed else "", "new_state": new_state}
