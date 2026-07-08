"""RSSWatcher — dispara em itens novos de um feed RSS/Atom, filtrando por
palavras-chave (opcional).

config: {
  "url": "https://…/feed.xml",   # obrigatório
  "keywords": ["python", "ia"]    # opcional: só itens cujo título/resumo contenha alguma
}
Compara os GUIDs/links vistos com o estado anterior. Primeira observação = linha
de base (não notifica).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx

from ...deep_search import _is_public_url

_UA = "Mozilla/5.0 (compatible; AIWorkspace-Watcher/1.0)"


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def _parse_items(xml: str) -> list[dict]:
    """Extrai itens de RSS (channel/item) ou Atom (entry). Robusto a namespaces."""
    items: list[dict] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return items

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].lower()

    for el in root.iter():
        if local(el.tag) not in ("item", "entry"):
            continue
        title = link = guid = summary = ""
        for child in el:
            name = local(child.tag)
            if name == "title":
                title = _text(child)
            elif name in ("guid", "id"):
                guid = _text(child)
            elif name == "link":
                # RSS: texto; Atom: atributo href
                link = _text(child) or (child.get("href") or "")
            elif name in ("description", "summary", "content"):
                summary = _text(child)
        key = guid or link or title
        if key:
            items.append({"key": key, "title": title, "link": link, "summary": summary})
    return items


async def check(config: dict, state: dict, deps) -> dict:
    url = (config.get("url") or "").strip()
    if not url:
        return {"changed": False, "new_state": state, "error": "informe a URL do feed"}
    if not _is_public_url(url):
        return {"changed": False, "new_state": state, "error": "URL inválida ou não pública"}

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": _UA}, timeout=10)
        if r.status_code != 200:
            return {"changed": False, "new_state": state, "error": f"HTTP {r.status_code}"}
        items = _parse_items(r.text)
    except Exception as exc:  # noqa: BLE001
        return {"changed": False, "new_state": state, "error": f"falha ao buscar o feed: {exc}"}

    if not items:
        return {"changed": False, "new_state": state, "error": "feed vazio ou inválido"}

    keywords = [k.strip().lower() for k in (config.get("keywords") or []) if str(k).strip()]

    def matches(it: dict) -> bool:
        if not keywords:
            return True
        blob = (it["title"] + " " + it["summary"]).lower()
        return any(k in blob for k in keywords)

    seen = set(state.get("seen") or [])
    all_keys = [it["key"] for it in items]
    new_state = {"seen": all_keys[:200]}

    # primeira observação: linha de base
    if not state.get("seen"):
        return {"changed": False, "new_state": new_state}

    new_items = [it for it in items if it["key"] not in seen and matches(it)]
    if not new_items:
        return {"changed": False, "new_state": new_state}

    listing = "\n".join(f"- {it['title']} ({it['link']})" for it in new_items[:8])
    return {
        "changed": True,
        "summary_input": f"Novos itens no feed:\n{listing}",
        "new_state": new_state,
    }
