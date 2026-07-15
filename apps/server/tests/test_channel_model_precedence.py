"""Precedência do modelo nos canais: o ModelConfig acoplado MANDA.

`conn.model` é um snapshot que o painel grava na hora da escolha; quando o usuário
troca o modelo do agente depois (ex.: Ayla flash → pro), a conexão ficava presa no
snapshot velho — visto ao vivo no WhatsApp (a troca "não pegava")."""
from __future__ import annotations

from types import SimpleNamespace


def _resolved(conn_model: str, mc) -> str:
    # a expressão usada nos 3 services (whatsapp/telegram/discord)
    return (mc.base_model if mc else "") or conn_model


def test_modelconfig_wins_over_stale_snapshot():
    mc = SimpleNamespace(base_model="deepseek/deepseek-v4-pro")
    assert _resolved("deepseek/deepseek-v4-flash", mc) == "deepseek/deepseek-v4-pro"


def test_connection_model_used_without_modelconfig():
    assert _resolved("openai/gpt-5-mini", None) == "openai/gpt-5-mini"


def test_empty_base_model_falls_back_to_connection():
    mc = SimpleNamespace(base_model="")
    assert _resolved("deepseek/deepseek-v4-flash", mc) == "deepseek/deepseek-v4-flash"


def test_services_use_the_fixed_expression():
    """Trava a expressão no código-fonte dos 3 services (regressão barata)."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "aiworkspace" / "integrations"
    for svc in ("whatsapp_service.py", "telegram_service.py", "discord_service.py"):
        src = (root / svc).read_text(encoding="utf-8")
        assert 'model = (mc.base_model if mc else "") or conn.model' in src, svc
        assert "model = conn.model or" not in src, svc
