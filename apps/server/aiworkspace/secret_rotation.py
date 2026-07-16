"""Rotação do APP_SECRET — re-cifra os segredos do banco de uma chave para outra.

Por que é preciso: o APP_SECRET é a RAIZ de confiança da criptografia (o Fernet é
derivado dele por HKDF, `crypto._fernet`). Ele cifra:
  - `user_secrets.ciphertext` (chaves de API — token Fernet cru, sem prefixo);
  - todas as colunas `EncryptedText` (tokens de bot, refresh tokens OAuth, prompts,
    mensagens…) — prefixadas com `enc:v1:`.
Trocar o APP_SECRET no .env SEM re-cifrar deixa tudo isso ILEGÍVEL (a app trata como
"não configurado"). Também invalida os JWT (todos re-logam) — isso é esperado.

IMPORTANTE — o que o APP_SECRET NÃO faz: ele NÃO dá acesso ao banco. A conexão usa
POSTGRES_PASSWORD/DATABASE_URL. Trocá-lo não impede o Postgres de subir nem de ser
lido; só torna os VALORES CIFRADOS indecifráveis. Esta ferramenta resolve isso:
decifra com a chave ANTIGA e recifra com a NOVA, direto nas colunas (sem passar pela
cripto transparente do ORM, que usaria a chave corrente).

Uso (dentro do container, durante uma janela de manutenção curta):
    # confere o que seria feito, sem escrever (recomendado ANTES):
    NEW_APP_SECRET=<nova> python -m aiworkspace.secret_rotation --dry-run
    # aplica a rotação no banco:
    NEW_APP_SECRET=<nova> python -m aiworkspace.secret_rotation
    # depois: troque APP_SECRET no .env para <nova> e reinicie o server.

A chave ANTIGA é lida do APP_SECRET corrente (o .env em que a app roda hoje). A NOVA
vem de NEW_APP_SECRET (env) ou, se ausente, é pedida pela entrada padrão (não fica no
histórico/`ps`). É RESUMÍVEL: valores já recifrados (que a chave NOVA já decifra) são
pulados, então rodar de novo após uma interrupção é seguro.
"""

from __future__ import annotations

import base64
import sys
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import create_engine, text

from . import models  # noqa: F401  — popula o registry (todas as tabelas)
from .config import get_settings
from .crypto import EncryptedText, _FIELD_PREFIX
from .db import Base


def _fernet_for(secret: str) -> Fernet:
    """Fernet derivado de um APP_SECRET — MESMA derivação de `crypto._fernet`
    (se aquela mudar, esta precisa acompanhar, senão a rotação corrompe tudo)."""
    kdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"ai-workspace-secret-encryption",
        info=b"fernet-key",
    )
    key = kdf.derive(secret.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


@dataclass
class _Target:
    table: str
    column: str
    pk: str
    prefixed: bool  # True = coluna EncryptedText (enc:v1:); False = token Fernet cru


def _discover_targets() -> list[_Target]:
    """Toda coluna cifrada do banco, descoberta pelo ORM (não some se um modelo
    novo aparecer). `user_secrets.ciphertext` é adicionado à mão (Text cru)."""
    targets: list[_Target] = []
    seen: set[tuple[str, str]] = set()
    for mapper in Base.registry.mappers:
        tbl = mapper.local_table
        if tbl is None or not tbl.primary_key.columns:
            continue
        pk = list(tbl.primary_key.columns)[0].name
        for col in tbl.columns:
            if isinstance(col.type, EncryptedText) and (tbl.name, col.name) not in seen:
                targets.append(_Target(tbl.name, col.name, pk, prefixed=True))
                seen.add((tbl.name, col.name))
    # segredos de API: token Fernet CRU (sem o prefixo enc:v1:)
    targets.append(_Target("user_secrets", "ciphertext", "id", prefixed=False))
    return targets


def _rotate_value(raw: str, old: Fernet, new: Fernet, prefixed: bool) -> str | None:
    """Devolve o novo valor cifrado, ou None se nada a fazer (legado/ já rotacionado).
    Levanta InvalidToken se nem a chave antiga nem a nova conseguem decifrar."""
    token = raw[len(_FIELD_PREFIX):] if prefixed else raw
    if prefixed and not raw.startswith(_FIELD_PREFIX):
        return None  # legado (texto puro gravado antes da criptografia): não mexe
    try:
        new.decrypt(token.encode("utf-8"))
        return None  # já está na chave nova → resumível, pula
    except InvalidToken:
        pass
    plain = old.decrypt(token.encode("utf-8"))  # pode levantar: valor órfão/chave errada
    fresh = new.encrypt(plain).decode("utf-8")
    return (_FIELD_PREFIX + fresh) if prefixed else fresh


def rotate(old_secret: str, new_secret: str, *, dry_run: bool) -> dict:
    old, new = _fernet_for(old_secret), _fernet_for(new_secret)
    eng = create_engine(get_settings().sync_database_url)
    summary: dict[str, dict] = {}
    total_changed = total_skipped = total_failed = 0
    with eng.begin() as conn:
        for tg in _discover_targets():
            rows = conn.execute(
                text(f'SELECT "{tg.pk}" AS id, "{tg.column}" AS val FROM "{tg.table}" '
                     f'WHERE "{tg.column}" IS NOT NULL AND "{tg.column}" <> \'\'')
            ).all()
            changed = skipped = failed = 0
            for rid, val in rows:
                try:
                    newval = _rotate_value(val, old, new, tg.prefixed)
                except InvalidToken:
                    failed += 1  # não decifra com nenhuma das chaves — deixa como está
                    continue
                if newval is None:
                    skipped += 1
                    continue
                if not dry_run:
                    conn.execute(
                        text(f'UPDATE "{tg.table}" SET "{tg.column}" = :v WHERE "{tg.pk}" = :id'),
                        {"v": newval, "id": rid},
                    )
                changed += 1
            summary[f"{tg.table}.{tg.column}"] = {"changed": changed, "skipped": skipped, "failed": failed}
            total_changed += changed; total_skipped += skipped; total_failed += failed
        if dry_run:
            conn.rollback()  # garante que nada foi escrito no dry-run
    eng.dispose()
    return {"targets": summary, "changed": total_changed, "skipped": total_skipped, "failed": total_failed}


def _read_new_secret() -> str:
    import os
    val = os.environ.get("NEW_APP_SECRET", "").strip()
    if val:
        return val
    # sem env: pede pela entrada padrão (não vaza em `ps`/histórico)
    print("Nova APP_SECRET (não será ecoada no histórico):", file=sys.stderr, flush=True)
    return sys.stdin.readline().strip()


def main() -> int:
    dry = "--dry-run" in sys.argv
    old = get_settings().app_secret
    new = _read_new_secret()
    if not new:
        print("ERRO: informe a nova chave em NEW_APP_SECRET ou pela entrada padrão.", file=sys.stderr)
        return 2
    if new == old:
        print("ERRO: a nova chave é igual à atual — nada a rotacionar.", file=sys.stderr)
        return 2
    if len(new) < 32:
        print("AVISO: a nova chave é curta (<32 chars). Gere com "
              "`python -c \"import secrets;print(secrets.token_urlsafe(48))\"`.", file=sys.stderr)
    res = rotate(old, new, dry_run=dry)
    label = "DRY-RUN (nada escrito)" if dry else "APLICADO"
    print(f"\n== Rotação do APP_SECRET — {label} ==")
    for name, c in res["targets"].items():
        if c["changed"] or c["failed"]:
            print(f"  {name}: recifrados={c['changed']} pulados={c['skipped']} falhas={c['failed']}")
    print(f"TOTAL: recifrados={res['changed']} pulados={res['skipped']} falhas={res['failed']}")
    if res["failed"]:
        print("\nATENÇÃO: houve valores que não decifram com a chave atual (podem ser de um "
              "APP_SECRET ainda mais antigo, ou dados corrompidos). Eles foram DEIXADOS como "
              "estavam. Reveja antes de trocar o .env.", file=sys.stderr)
    if dry:
        print("\nDry-run OK. Rode sem --dry-run para aplicar; depois troque APP_SECRET no .env e reinicie.")
    else:
        print("\nFEITO. Agora: 1) troque APP_SECRET no .env para a nova chave; "
              "2) reinicie o server (docker compose up -d server). Todos re-logam.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
