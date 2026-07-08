"""Criptografia de segredos por usuário (Fernet).

A chave do Fernet é derivada do APP_SECRET via HKDF, então não precisamos
guardar uma chave separada — o APP_SECRET do .env é a raiz de confiança.
Segredos (ex.: chave do OpenRouter) ficam cifrados em repouso no banco.
"""

from __future__ import annotations

import base64
import json
import os
from functools import lru_cache

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from .config import get_settings


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    kdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"ai-workspace-secret-encryption",
        info=b"fernet-key",
    )
    key = kdf.derive(settings.app_secret.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")


# --------------------------------------------------------------------------- #
# Criptografia de CAMPO (em repouso): usado pelo TypeDecorator EncryptedText.
# Prefixo marca o texto cifrado; sem o prefixo, trata como legado (texto puro),
# então habilitar a criptografia não quebra linhas já existentes no banco.
# --------------------------------------------------------------------------- #
_FIELD_PREFIX = "enc:v1:"


def encrypt_field(plaintext: str | None) -> str | None:
    if plaintext is None:
        return None
    if not plaintext:
        return plaintext  # string vazia não precisa cifrar
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")
    return _FIELD_PREFIX + token


def decrypt_field(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.startswith(_FIELD_PREFIX):
        return value  # legado (texto puro gravado antes da criptografia)
    try:
        return _fernet().decrypt(value[len(_FIELD_PREFIX):].encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return value  # não decifrável (APP_SECRET mudou?): devolve como está


# --------------------------------------------------------------------------- #
# Criptografia por SENHA (exportação): chave derivada da senha do usuário via
# scrypt; o salt vai junto no blob. O servidor NÃO guarda essa senha.
# --------------------------------------------------------------------------- #
def _password_key(password: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1)
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def encrypt_with_password(plaintext: str, password: str) -> str:
    """Cifra `plaintext` com uma senha; devolve um blob JSON auto-descritivo."""
    salt = os.urandom(16)
    token = Fernet(_password_key(password, salt)).encrypt(plaintext.encode("utf-8"))
    return json.dumps({
        "aw_enc": 1,
        "kdf": "scrypt",
        "salt": base64.b64encode(salt).decode("ascii"),
        "data": token.decode("ascii"),
    })


def is_encrypted_blob(text: str) -> bool:
    try:
        obj = json.loads(text)
        return isinstance(obj, dict) and obj.get("aw_enc") == 1 and "salt" in obj and "data" in obj
    except (ValueError, TypeError):
        return False


def decrypt_with_password(blob: str, password: str) -> str:
    """Decifra um blob de `encrypt_with_password`. Levanta ValueError se a senha
    estiver errada ou o blob for inválido."""
    try:
        obj = json.loads(blob)
        salt = base64.b64decode(obj["salt"])
        token = obj["data"].encode("ascii")
    except (ValueError, KeyError, TypeError):
        raise ValueError("arquivo de exportação inválido")
    try:
        return Fernet(_password_key(password, salt)).decrypt(token).decode("utf-8")
    except InvalidToken:
        raise ValueError("senha incorreta")


class EncryptedText(TypeDecorator):
    """Coluna TEXT cifrada em repouso (transparente à aplicação).

    Escrita → cifra (Fernet/APP_SECRET); leitura → decifra. Valores legados (texto
    puro, gravados antes da criptografia) são lidos normalmente. Protege o dump do
    banco: sem o APP_SECRET, o conteúdo é ilegível."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_field(value)

    def process_result_value(self, value, dialect):
        return decrypt_field(value)
