"""2FA (TOTP), rotação do APP_SECRET e auditoria — lógica pura, hermética."""
from __future__ import annotations

import pyotp

from aiworkspace import twofa_service
from aiworkspace.crypto import _FIELD_PREFIX
from aiworkspace.secret_rotation import _fernet_for, _rotate_value


# ------------------------------- 2FA (TOTP) ----------------------------------

def test_totp_correct_code_passes_wrong_fails():
    secret = twofa_service.new_secret()
    assert twofa_service.verify(secret, pyotp.TOTP(secret).now())
    assert not twofa_service.verify(secret, "000000")
    assert not twofa_service.verify(secret, "")
    assert not twofa_service.verify(None, "123456")


def test_totp_tolerates_spaces_and_bad_input():
    secret = twofa_service.new_secret()
    code = pyotp.TOTP(secret).now()
    assert twofa_service.verify(secret, f" {code} ")
    assert not twofa_service.verify(secret, "abcdef")  # não-numérico não derruba


def test_provisioning_uri_and_qr():
    secret = twofa_service.new_secret()
    uri = twofa_service.provisioning_uri(secret, "user@x.com")
    # o issuer é só o RÓTULO exibido no app autenticador; a verificação usa apenas o
    # segredo. Renomear o produto NÃO invalida um 2FA já cadastrado — quem já tinha
    # continua vendo o nome antigo na lista do autenticador, e o código segue valendo.
    assert uri.startswith("otpauth://totp/") and "AI%20Workspace" in uri
    assert twofa_service.qr_data_url(secret, "user@x.com").startswith("data:image/png;base64,")


# -------------------------- rotação do APP_SECRET ----------------------------

OLD, NEW = "old-app-secret-aaaaaaaaaaaaaaaaaaaaaaaa", "new-app-secret-bbbbbbbbbbbbbbbbbbbbbbbb"


def test_rotate_bare_fernet_value():
    """user_secrets.ciphertext: token cru (sem prefixo) recifra e passa a decifrar
    só com a chave nova."""
    fo, fn = _fernet_for(OLD), _fernet_for(NEW)
    ct = fo.encrypt(b"minha-chave-de-api").decode()
    rotated = _rotate_value(ct, fo, fn, prefixed=False)
    assert rotated is not None
    assert fn.decrypt(rotated.encode()) == b"minha-chave-de-api"


def test_rotate_prefixed_encryptedtext():
    fo, fn = _fernet_for(OLD), _fernet_for(NEW)
    val = _FIELD_PREFIX + fo.encrypt(b"token-do-bot").decode()
    rotated = _rotate_value(val, fo, fn, prefixed=True)
    assert rotated.startswith(_FIELD_PREFIX)
    assert fn.decrypt(rotated[len(_FIELD_PREFIX):].encode()) == b"token-do-bot"


def test_rotate_skips_legacy_plaintext():
    """Valor legado (texto puro, sem o prefixo) não é tocado."""
    fo, fn = _fernet_for(OLD), _fernet_for(NEW)
    assert _rotate_value("texto-puro-legado", fo, fn, prefixed=True) is None


def test_rotate_is_resumable_skips_already_new():
    """Valor já na chave nova é pulado (rodar de novo é seguro)."""
    fo, fn = _fernet_for(OLD), _fernet_for(NEW)
    already = _FIELD_PREFIX + fn.encrypt(b"x").decode()
    assert _rotate_value(already, fo, fn, prefixed=True) is None


def test_rotation_covers_all_encrypted_columns():
    """A descoberta acha as colunas cifradas conhecidas (não pode esquecer nenhuma)."""
    from aiworkspace.secret_rotation import _discover_targets
    names = {f"{t.table}.{t.column}" for t in _discover_targets()}
    for expected in ("user_secrets.ciphertext", "whatsapp_connections.access_token",
                     "telegram_connections.bot_token", "discord_connections.bot_token",
                     "github_accounts.token", "google_accounts.refresh_token",
                     "messages.content", "chats.system_prompt", "users.totp_secret"):
        assert expected in names, expected
