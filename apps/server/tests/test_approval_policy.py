"""Quem aprova ações sensíveis (interaction.confirm_gate).

Antes, "background" (automação/canal/API) PULAVA a confirmação: sem humano, a ação
rodava direto. Isso transformava a ausência de pessoa em autorização — uma automação
que lê e-mails podia ser levada por um e-mail plantado a enviar/apagar coisas. Agora:
- chat na tela: cartão Confirmar/Cancelar (como antes);
- canal com lista de remetentes: a pessoa confirma na própria conversa;
- sem ninguém (automação, API, canal aberto): só o que o dono liberou antes;
  `confirm=true` vindo do modelo não conta."""
from __future__ import annotations

import contextvars
import re
from pathlib import Path
from types import SimpleNamespace

from aiworkspace.tools import interaction, toolctx

ROOT = Path(__file__).resolve().parents[1]


def _gate(mode, allowed=(), **kw):
    def go():
        toolctx.approval.set(mode)
        toolctx.preauthorized.set(frozenset(allowed))
        return interaction.confirm_gate("google", "Enviar e-mail?", **kw)
    return contextvars.copy_context().run(go)


def test_interactive_keeps_the_confirm_card():
    assert _gate("interactive", required=True, confirmed=False)["kind"] == "ask"
    assert _gate("interactive", required=True, confirmed=True) is None
    assert _gate("interactive", required=False, confirmed=False) is None


def test_channel_asks_in_the_conversation():
    out = _gate("conversation", required=True, confirmed=False)
    assert out["needs_confirmation"] == "Enviar e-mail?"
    assert _gate("conversation", required=True, confirmed=True) is None


def test_unattended_blocks_unless_preauthorized_even_with_confirm_true():
    # o modelo "confirmando" sozinho não vale: não há quem tenha confirmado
    out = _gate("unattended", required=False, confirmed=True)
    assert out["needs_approval"] == "google" and "bloqueada" in out["error"]
    assert _gate("unattended", allowed=("google",), required=True, confirmed=False) is None
    assert _gate("unattended", allowed=("*",), required=True, confirmed=False) is None
    assert _gate("unattended", allowed=("tuya",), required=True, confirmed=False)["needs_approval"] == "google"


def test_turn_session_derives_mode():
    from aiworkspace.chat.orchestrator import TurnSession
    assert TurnSession(user_id="u").approval_mode() == "interactive"
    assert TurnSession(user_id="u", background=True).approval_mode() == "unattended"
    assert TurnSession(user_id="u", background=True, approval="conversation").approval_mode() == "conversation"


def test_open_channel_is_unattended_restricted_channel_is_conversation():
    from aiworkspace.integrations import discord_service, slack_channel_service, telegram_service, whatsapp_service
    wa = whatsapp_service.approval_mode
    assert wa(SimpleNamespace(filters={"policy": "allow", "allow": ["5583999999999"]})) == "conversation"
    assert wa(SimpleNamespace(filters={"policy": "all", "allow": ["5583999999999"]})) == "unattended"
    assert wa(SimpleNamespace(filters={})) == "unattended"
    for svc in (telegram_service, discord_service, slack_channel_service):
        assert svc.approval_mode(SimpleNamespace(filters={"allow": ["victor"]})) == "conversation"
        assert svc.approval_mode(SimpleNamespace(filters={"allow": []})) == "unattended"


def test_no_confirmation_is_skipped_just_because_the_turn_is_background():
    src = (ROOT / "aiworkspace/tools/sift_service.py").read_text("utf-8")
    ruins = [ln.strip() for ln in src.splitlines()
             if "background.get()" in ln and ("confirm" in ln.lower() or "truthy" in ln)]
    assert not ruins, f"confirmação pulada por background: {ruins}"
    assert src.count("confirm_gate(") >= 9


def test_automation_editor_categories_match_server():
    ts = (ROOT.parent / "web/components/AutomationEditor.tsx").read_text("utf-8")
    bloco = ts[ts.index("const ACTION_CATEGORIES"):]
    bloco = bloco[:bloco.index("];")]
    front = set(re.findall(r'key: "([a-z_]+)"', bloco))
    assert front == set(interaction.ACTION_CATEGORIES)


def test_automation_only_passes_known_categories():
    from aiworkspace.automation.runner import _allowed_actions
    a = SimpleNamespace(options={"allowed_actions": ["google", "*", "hack", 3]})
    assert _allowed_actions(a) == ("google",)  # "*" só a chave de API pode ter
    assert _allowed_actions(SimpleNamespace(options=None)) == ()


def test_api_key_needs_explicit_actions_scope():
    from aiworkspace.api import keys_service
    assert "actions" in keys_service.ALL_SCOPES and "actions" not in keys_service.DEFAULT_SCOPES
    src = (ROOT / "aiworkspace/api/runner.py").read_text("utf-8")
    assert 'has_scope(ctx.key, "actions")' in src
