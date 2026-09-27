"""Linha ↔ forma de viagem.

As linhas saem do Postgres como JSON (`to_jsonb`) e voltam por `jsonb_populate_record`
— então uuid, datas, bytea, vetores e arrays viajam sem código por tipo.

O que precisa de tradução:
  - SEGREDOS: cada instância cifra com a própria chave (derivada do APP_SECRET dela).
    Na ida, o valor é decifrado e marcado ({"$enc": …} para colunas EncryptedText,
    {"$f": …} para tokens Fernet crus, inclusive dentro de JSON); na volta, recifrado
    com a chave de quem recebe. Em trânsito, o lote inteiro vai cifrado com o segredo
    do par (ver transport.py).
  - CONTAS: o `user_id` de lá vira o de cá pelo e-mail (user_map). Linha de conta que
    não existe dos dois lados não entra.
  - Colunas que apontam para tabelas fora da sync não viajam (cada lado guarda a sua).
  - LINKS ASSINADOS no conteúdo (anexo, imagem gerada, documento da base) levam um
    token da chave de quem criou: na chegada são reassinados com a chave local, senão
    abririam "Token inválido" na outra instância.
"""

from __future__ import annotations

import re
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from ..crypto import _FIELD_PREFIX
from .tables import RAW_MEMORIES, TableInfo

_FERNET_PREFIX = "gAAAAA"

_SIGNED = re.compile(
    r"(?:https?://[^/\s)\"'<>]+)?/(?P<kind>uploads|images|knowledge/docs)/(?P<id>[0-9a-fA-F-]{36})"
    r"(?P<raw>/raw)?\?t=[A-Za-z0-9._\-]+"
)


def _resign_one(m: re.Match) -> str:
    kind, rid = m.group("kind"), m.group("id")
    if kind == "uploads":
        from ..uploads_service import sign_url
        return sign_url(rid)
    if kind == "images":
        from ..providers.image_gen import sign_image_url
        return sign_image_url(rid)
    if m.group("raw"):
        from ..knowledge.links import sign_doc_url
        return sign_doc_url(rid)
    return m.group(0)


def resign(value: str) -> str:
    """Troca os tokens dos links assinados pelos desta instância."""
    return _SIGNED.sub(_resign_one, value) if "?t=" in value else value


def _open(token: str, fernet: Fernet) -> str | None:
    try:
        return fernet.decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


def _out_json(obj: Any, fernet: Fernet) -> Any:
    if isinstance(obj, dict):
        return {k: _out_json(v, fernet) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_out_json(v, fernet) for v in obj]
    if isinstance(obj, str) and obj.startswith(_FERNET_PREFIX) and len(obj) > 40:
        plain = _open(obj, fernet)
        return {"$f": plain} if plain is not None else obj
    return obj


def _in_json(obj: Any, fernet: Fernet) -> Any:
    if isinstance(obj, dict):
        if len(obj) == 1 and "$f" in obj and isinstance(obj["$f"], str):
            return fernet.encrypt(obj["$f"].encode("utf-8")).decode("utf-8")
        if len(obj) == 1 and "$enc" in obj and isinstance(obj["$enc"], str):
            return _FIELD_PREFIX + fernet.encrypt(resign(obj["$enc"]).encode("utf-8")).decode("utf-8")
        return {k: _in_json(v, fernet) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_in_json(v, fernet) for v in obj]
    if isinstance(obj, str):
        return resign(obj)
    return obj


def outgoing(info: TableInfo, row: dict, fernet: Fernet) -> dict:
    """Linha desta instância → forma de viagem (segredos em claro, marcados)."""
    out = {k: v for k, v in row.items() if k not in info.dropped_cols}
    for c in info.encrypted_cols:
        v = out.get(c)
        if isinstance(v, str) and v.startswith(_FIELD_PREFIX):
            plain = _open(v[len(_FIELD_PREFIX):], fernet)
            if plain is not None:
                out[c] = {"$enc": plain}
    for c in info.raw_fernet_cols:
        v = out.get(c)
        if isinstance(v, str):
            plain = _open(v, fernet)
            if plain is not None:
                out[c] = {"$f": plain}
    for c in info.json_cols:
        if c in out:
            out[c] = _out_json(out[c], fernet)
    return out


def incoming(info: TableInfo, row: dict, fernet: Fernet, user_map: dict[str, str]) -> dict | None:
    """Forma de viagem → linha desta instância. None = conta que não é comum aos dois."""
    out = dict(row)
    for c in info.user_cols:
        v = out.get(c)
        if v is None:
            continue
        local = user_map.get(str(v))
        if local is None:
            return None
        out[c] = local
    if info.name == RAW_MEMORIES:
        payload = out.get("payload") if isinstance(out.get("payload"), dict) else {}
        dono = payload.get("user_id")
        local = user_map.get(str(dono)) if dono else None
        if local is None:
            return None
        out["payload"] = {**payload, "user_id": local}
    for c, v in list(out.items()):
        if isinstance(v, (dict, list, str)):
            out[c] = _in_json(v, fernet)
    return {k: v for k, v in out.items() if k in info.columns}


def owner_of(info: TableInfo, row: dict) -> str | None:
    if info.name == RAW_MEMORIES:
        p = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        return str(p.get("user_id")) if p.get("user_id") else None
    v = row.get("user_id")
    return str(v) if v else None
