from __future__ import annotations

import re
import uuid
from typing import Any

from pydantic import BaseModel, Field, field_validator

# Validação leve: app local-first deve aceitar domínios locais (admin@local,
# user@localhost) que o email-validator estrito rejeita. Exigimos só um formato
# básico "algo@algo".
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


class RegisterIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("e-mail inválido (use o formato nome@dominio)")
        return v


class LoginIn(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def _normalize(cls, v: str) -> str:
        return v.strip().lower()

    password: str
    # 2º fator (TOTP): só exigido se o usuário tiver 2FA ativo. Opcional na 1ª etapa.
    totp_code: str | None = Field(default=None, max_length=12)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    is_active: bool
    status: str = "active"
    default_model: str | None = None
    profile: dict = Field(default_factory=dict)
    totp_enabled: bool = False

    class Config:
        from_attributes = True


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class ProfileIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    about: str | None = Field(default=None, max_length=2000)
    gender: str | None = Field(default=None, max_length=40)
    birthdate: str | None = Field(default=None, max_length=32)
    webhook_url: str | None = Field(default=None, max_length=500)
    theme: str | None = Field(default=None, max_length=20)
    language: str | None = Field(default=None, max_length=20)
    notifications: bool | None = None
    system_prompt: str | None = Field(default=None, max_length=20000)
    # "Guarda de tokens": avisa quando uma resposta passa deste nº de tokens (0 = off)
    token_warn: int | None = Field(default=None, ge=0, le=10_000_000)
    # avatar como data URL (imagem redimensionada no cliente p/ ~256px)
    avatar: str | None = Field(default=None, max_length=400_000)
    # favoritos do seletor de modelos: chaves "ext:<id>" | "custom:<id>"
    favorite_models: list[str] | None = Field(default=None, max_length=200)
    # modelos fixados na barra lateral: mesmas chaves "ext:<id>" | "custom:<id>"
    pinned_models: list[str] | None = Field(default=None, max_length=200)
    # integração "Extração de Texto": formatos habilitados + filtros de token
    text_extraction: dict[str, Any] | None = None
    # conexão "Pesquisa na Web": mecanismo(s), multi, filtros
    web_search: dict[str, Any] | None = None
    # conexão "Finanças": ordem de provedores de cotação + fallback web
    finance: dict[str, Any] | None = None
    # integração "Deep Search": modelo interno + amplitude/profundidade
    deep_search: dict[str, Any] | None = None
    # seção "Interface": barra lateral, geração de título de chat, etc.
    interface: dict[str, Any] | None = None
    # atalhos de teclado personalizados: { actionId: {keys: "mod+k", enabled: bool} }
    shortcuts: dict[str, Any] | None = None
    # seção "Segurança": {confirm_actions: bool} — pedir confirmação antes de
    # escritas sensíveis (e-mail/agenda/casa). Padrão desligado.
    security: dict[str, Any] | None = None
    # orçamento pessoal opt-in: {enabled, monthly_usd, mode: "warn"|"pause"}
    budget: dict[str, Any] | None = None
    # concluiu (ou pulou) o wizard de primeiro uso
    onboarded: bool | None = None

    @field_validator("avatar")
    @classmethod
    def _avatar_is_image_data_url(cls, v: str | None) -> str | None:
        if v is not None and v != "" and not v.startswith("data:image/"):
            raise ValueError("avatar deve ser uma imagem (data URL)")
        return v

    @field_validator("favorite_models", "pinned_models")
    @classmethod
    def _model_keys(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        return [k[:300] for k in v if isinstance(k, str) and k][:200]
