"""2FA por TOTP (Google/Microsoft Authenticator, Authy…).

O segredo base32 é gerado no servidor, cifrado em repouso (User.totp_secret via
EncryptedText) e só passa a valer (`totp_enabled=True`) depois que o usuário
confirma um código — provando que pareou o app. A verificação aceita uma janela de
±1 passo (30s) para tolerar relógio ligeiramente fora."""

from __future__ import annotations

import base64
import io

import pyotp
import qrcode

_ISSUER = "Singularity AI"


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=_ISSUER)


def qr_data_url(secret: str, email: str) -> str:
    """PNG do QR de pareamento como data: URL (renderizável direto num <img>)."""
    img = qrcode.make(provisioning_uri(secret, email))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def verify(secret: str | None, code: str) -> bool:
    """True se `code` (6 dígitos) confere com o segredo. `valid_window=1` tolera
    um passo de defasagem de relógio."""
    if not secret or not code:
        return False
    try:
        return pyotp.TOTP(secret).verify(code.strip().replace(" ", ""), valid_window=1)
    except Exception:  # noqa: BLE001 - código malformado = inválido, nunca 500
        return False
