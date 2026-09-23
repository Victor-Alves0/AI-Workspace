"""Criptografia de segredos por usuário (Fernet).

A chave do Fernet é derivada do APP_SECRET via HKDF, então não precisamos
guardar uma chave separada — o APP_SECRET do .env é a raiz de confiança.
Segredos (ex.: chave do OpenRouter) ficam cifrados em repouso no banco.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from functools import lru_cache

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
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
        # ⚠️ NÃO RENOMEIE ESTE SALT. Ele entra na derivação da chave: mudar um byte
        # muda a chave, e TODO segredo já cifrado no banco (tokens do Google, GitHub,
        # Slack, Notion, Tuya, chaves de API) vira ilegível — sem erro claro, só
        # falha de decrypt. O produto passou a se chamar "AI Workspace"; este
        # literal continua com o nome antigo de propósito, e assim deve ficar.
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


# --------------------------------------------------------------------------- #
# Cifra do ARQUIVO DE BACKUP (streaming): AES-256-CTR com chave derivada do
# APP_SECRET. Streaming (chunk a chunk) para não carregar o dump inteiro em
# memória. Cabeçalho = MAGIC + nonce(16). Assim o dump do pg_dump — que sozinho
# é texto claro — vira ciphertext em repouso; restaurar exige o mesmo APP_SECRET
# (já necessário para os campos cifrados). Sem o header = backup legado (claro).
# --------------------------------------------------------------------------- #
BACKUP_MAGIC = b"AIWBK1\n"     # 7 bytes - payload = pg_dump (formato antigo)
# AIWBK2: payload e um TAR (bundle) com `database.dump` + a arvore `codespace/`
# (arquivos do Codespace, que vivem em disco fora do banco). Mesmo tamanho (7 bytes)
# que o magic antigo, entao a leitura do nonce (16 bytes seguintes) nao muda.
BACKUP_MAGIC_V2 = b"AIWBK2\n"  # 7 bytes - payload = tar bundle (db + codespace)


def _backup_key_for(secret: str) -> bytes:
    kdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        # ⚠️ NÃO RENOMEIE (mesma razão do salt acima): mudar aqui torna ilegível
        # todo backup já exportado — inclusive os que o usuário guardou para migrar.
        salt=b"ai-workspace-backup-encryption",
        info=b"backup-aes-ctr",
    )
    return kdf.derive(secret.encode("utf-8"))


@lru_cache
def _backup_key() -> bytes:
    return _backup_key_for(get_settings().app_secret)


def backup_encryptor(magic: bytes = BACKUP_MAGIC) -> tuple[bytes, "object"]:
    """(header, encryptor). Emita o header primeiro, depois `encryptor.update(chunk)`
    para cada bloco e `encryptor.finalize()` no fim. `magic` escolhe o formato do
    payload (BACKUP_MAGIC = pg_dump; BACKUP_MAGIC_V2 = tar bundle)."""
    nonce = os.urandom(16)
    enc = Cipher(algorithms.AES(_backup_key()), modes.CTR(nonce)).encryptor()
    return magic + nonce, enc


def backup_decryptor(nonce: bytes, source_secret: str | None = None) -> "object":
    """Decifra um AIWBK1/2. `source_secret` = APP_SECRET da instalação que gerou o
    backup (migração de um backup antigo, sem senha); None = o desta instalação."""
    key = _backup_key_for(source_secret) if source_secret else _backup_key()
    return Cipher(algorithms.AES(key), modes.CTR(nonce)).decryptor()


# --------------------------------------------------------------------------- #
# AIWBK3 — backup para MIGRAR entre instalações (servidor → desktop, p. ex.).
# Cifrado por uma SENHA escolhida na exportação (scrypt), não pelo APP_SECRET: a
# instalação de destino não precisa ter o mesmo APP_SECRET (o desktop nem deixa
# trocá-lo). O bundle leva a chave de DADOS da origem (`keys.json`) — ela só decifra
# os segredos do banco (não assina sessões) — e a restauração os recifra para a
# chave local. Header = MAGIC + salt(16) + nonce(16) + check(16): o `check` acusa
# senha errada de cara (AES-CTR sozinho não detecta, só daria lixo).
# --------------------------------------------------------------------------- #
BACKUP_MAGIC_V3 = b"AIWBK3\n"
BACKUP_V3_HEADER = len(BACKUP_MAGIC_V3) + 48


def _v3_check(key: bytes) -> bytes:
    import hmac as _hmac

    return _hmac.new(key, b"aiworkspace-backup-v3", hashlib.sha256).digest()[:16]


def backup_password_encryptor(password: str) -> tuple[bytes, "object"]:
    salt, nonce = os.urandom(16), os.urandom(16)
    key = base64.urlsafe_b64decode(_password_key(password, salt))  # 32 bytes crus p/ o AES
    enc = Cipher(algorithms.AES(key), modes.CTR(nonce)).encryptor()
    return BACKUP_MAGIC_V3 + salt + nonce + _v3_check(key), enc


def backup_password_decryptor(header: bytes, password: str) -> "object":
    """Decifrador do AIWBK3; ValueError("senha incorreta") se a senha não confere."""
    import hmac as _hmac

    base = len(BACKUP_MAGIC_V3)
    salt, nonce, check = header[base:base + 16], header[base + 16:base + 32], header[base + 32:base + 48]
    key = base64.urlsafe_b64decode(_password_key(password, salt))  # 32 bytes crus p/ o AES
    if not _hmac.compare_digest(_v3_check(key), check):
        raise ValueError("senha incorreta")
    return Cipher(algorithms.AES(key), modes.CTR(nonce)).decryptor()


def data_key_b64() -> str:
    """Chave de DADOS desta instalação (a do Fernet dos segredos), p/ o bundle de
    migração. Não é o APP_SECRET: não assina sessões nem decifra outros backups."""
    return _fernet_key_b64(get_settings().app_secret)


def _fernet_key_b64(secret: str) -> str:
    kdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"ai-workspace-secret-encryption",
        info=b"fernet-key",
    )
    return base64.urlsafe_b64encode(kdf.derive(secret.encode("utf-8"))).decode()


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
