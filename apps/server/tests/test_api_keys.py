"""Chaves de API: credencial, políticas e limites.

Puro/hermético: nada de banco nem de rede. Cobre exatamente as decisões que, se
saírem erradas, viram falha de segurança — verificação da chave, estado, IP,
política de modelo e escopo de memória.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from aiworkspace.api import keys_service as ks
from aiworkspace.api import limits


def _key(**over):
    base = dict(
        id="11111111-1111-1111-1111-111111111111",
        name="k", prefix="abc", key_hash="", scopes=list(ks.DEFAULT_SCOPES),
        model_policy={"mode": "all"}, limits={}, memory={}, ip_allowlist=[],
        webhook={}, alerts_sent=[], enabled=True, expires_at=None, revoked_at=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# Credencial
# --------------------------------------------------------------------------- #

def test_generate_produces_parseable_key_and_matching_hash():
    plain, prefix, key_hash = ks.generate()
    parsed = ks.parse(plain)
    assert parsed is not None
    assert parsed[0] == prefix
    assert ks.verify(parsed[1], key_hash)


def test_generate_is_unique_across_calls():
    keys = {ks.generate()[0] for _ in range(50)}
    assert len(keys) == 50


def test_generated_key_always_round_trips():
    """Regressão: com um alfabeto que contivesse o delimitador '-', o parse dividia
    no lugar errado e rejeitava uma chave legítima — de forma aleatória, conforme o
    RNG. Muitas gerações seguidas tornam a falha determinística."""
    for _ in range(500):
        plain, prefix, key_hash = ks.generate()
        parsed = ks.parse(plain)
        assert parsed is not None, plain
        assert parsed[0] == prefix, plain
        assert ks.verify(parsed[1], key_hash), plain


def test_verify_rejects_wrong_secret():
    _, _, key_hash = ks.generate()
    assert ks.verify("nao-e-o-segredo", key_hash) is False


@pytest.mark.parametrize("raw", [
    "", "abc", "aw-", "aw-só-duas-partes",
    "sk-proj-parecido-com-openai-mas-nao-e",
    "aw-curto-x",                       # segredo curto demais
    "aw-abc-def ghi",                   # espaço no segredo
])
def test_parse_rejects_malformed(raw):
    assert ks.parse(raw) is None


def test_masked_never_leaks_the_secret():
    plain, prefix, _ = ks.generate()
    secret = plain.rsplit("-", 1)[-1]
    assert secret not in ks.masked(prefix)


# --------------------------------------------------------------------------- #
# Estado
# --------------------------------------------------------------------------- #

def test_key_state_active_by_default():
    assert ks.key_state(_key()) == "active"


def test_key_state_revoked_wins_over_everything():
    k = _key(revoked_at=datetime.now(timezone.utc), enabled=True)
    assert ks.key_state(k) == "revoked"


def test_key_state_disabled():
    assert ks.key_state(_key(enabled=False)) == "disabled"


def test_key_state_expired():
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert ks.key_state(_key(expires_at=past)) == "expired"


def test_key_state_future_expiry_still_active():
    future = datetime.now(timezone.utc) + timedelta(days=1)
    assert ks.key_state(_key(expires_at=future)) == "active"


def test_key_state_handles_naive_datetime_as_utc():
    """O banco pode devolver datetime sem tzinfo; comparar com um aware daria
    TypeError e derrubaria a autenticação inteira."""
    naive = (datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=None)
    assert ks.key_state(_key(expires_at=naive)) == "expired"


# --------------------------------------------------------------------------- #
# IP
# --------------------------------------------------------------------------- #

def test_ip_allowlist_empty_allows_all():
    assert ks.ip_allowed(_key(), "203.0.113.7") is True
    assert ks.ip_allowed(_key(), None) is True


def test_ip_allowlist_exact_and_cidr():
    k = _key(ip_allowlist=["203.0.113.7", "10.0.0.0/8"])
    assert ks.ip_allowed(k, "203.0.113.7") is True
    assert ks.ip_allowed(k, "10.4.5.6") is True
    assert ks.ip_allowed(k, "203.0.113.8") is False


def test_ip_allowlist_blocks_when_ip_unknown():
    """Sem IP identificado, uma allowlist configurada tem de FECHAR — senão bastaria
    esconder o IP de origem para contornar a restrição."""
    assert ks.ip_allowed(_key(ip_allowlist=["10.0.0.0/8"]), None) is False


def test_ip_allowlist_ignores_garbage_entries():
    k = _key(ip_allowlist=["nao-e-ip", "10.0.0.0/8"])
    assert ks.ip_allowed(k, "10.1.2.3") is True
    assert ks.ip_allowed(k, "8.8.8.8") is False


# --------------------------------------------------------------------------- #
# Modelos
# --------------------------------------------------------------------------- #

def test_model_policy_all_allows_anything():
    assert ks.model_allowed(_key(), "qualquer-coisa") is True


def test_model_policy_allow_restricts_to_list():
    k = _key(model_policy={"mode": "allow", "ids": ["assistente", "suporte"]})
    assert ks.model_allowed(k, "assistente") is True
    assert ks.model_allowed(k, "premium") is False


def test_model_policy_allow_does_not_admit_new_models():
    """O ponto do modo lista: um modelo que aparecer depois NÃO entra sozinho."""
    k = _key(model_policy={"mode": "allow", "ids": ["v1"]})
    assert ks.model_allowed(k, "v2-lancado-depois") is False


def test_default_model_read_from_policy():
    assert ks.default_model(_key(model_policy={"mode": "all", "default": "x"})) == "x"
    assert ks.default_model(_key()) == ""


# --------------------------------------------------------------------------- #
# Escopos e memória
# --------------------------------------------------------------------------- #

def test_has_scope():
    k = _key(scopes=["chat"])
    assert ks.has_scope(k, "chat") is True
    assert ks.has_scope(k, "memory:write") is False


def test_memory_mode_defaults_to_none_when_invalid():
    assert ks.memory_mode(_key(memory={})) == "none"
    assert ks.memory_mode(_key(memory={"mode": "inventado"})) == "none"


def test_memory_scope_isolation_between_keys():
    a = _key(id="aaa", memory={"mode": "key"})
    b = _key(id="bbb", memory={"mode": "key"})
    assert ks.memory_scope_id(a, "u1", None) != ks.memory_scope_id(b, "u1", None)


def test_memory_scope_end_user_separates_users():
    k = _key(memory={"mode": "end_user"})
    assert ks.memory_scope_id(k, "u1", "ana") != ks.memory_scope_id(k, "u1", "bruno")


def test_memory_scope_none_and_request_have_no_store():
    assert ks.memory_scope_id(_key(memory={"mode": "none"}), "u1", None) is None
    assert ks.memory_scope_id(_key(memory={"mode": "request"}), "u1", None) is None


# --------------------------------------------------------------------------- #
# Rate limit e concorrência (janela em memória)
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _clean_limits():
    limits.reset()
    yield
    limits.reset()


def test_rate_limit_allows_up_to_rpm_then_blocks():
    k = _key(limits={"rpm": 3})
    for _ in range(3):
        limits.check_rate(k)
    with pytest.raises(limits.LimitError) as exc:
        limits.check_rate(k)
    assert exc.value.code == "rate_limit_exceeded"
    assert exc.value.retry_after and exc.value.retry_after > 0


def test_rate_limit_absent_means_unlimited():
    k = _key(limits={})
    for _ in range(100):
        limits.check_rate(k)


def test_rate_limit_is_per_key():
    a, b = _key(id="a", limits={"rpm": 1}), _key(id="b", limits={"rpm": 1})
    limits.check_rate(a)
    limits.check_rate(b)  # a cota de uma chave não pode consumir a da outra
    with pytest.raises(limits.LimitError):
        limits.check_rate(a)


def test_concurrency_slots_are_released():
    k = _key(limits={"concurrency": 1})
    assert limits.acquire_slot(k) is True
    assert limits.acquire_slot(k) is False
    limits.release_slot(k)
    assert limits.acquire_slot(k) is True


def test_concurrency_absent_means_unlimited():
    k = _key(limits={})
    assert all(limits.acquire_slot(k) for _ in range(20))


def test_release_slot_never_goes_negative():
    """Um release a mais (erro de fluxo) não pode criar vagas fantasma."""
    k = _key(limits={"concurrency": 1})
    limits.release_slot(k)
    limits.release_slot(k)
    assert limits.acquire_slot(k) is True
    assert limits.acquire_slot(k) is False


# --------------------------------------------------------------------------- #
# Alertas de orçamento
# --------------------------------------------------------------------------- #

def test_budget_alerts_cross_marks_once():
    k = _key(limits={"budget_usd": 10.0}, alerts_sent=[])
    assert limits.budget_alerts(k, 5.5) == [50]
    k.alerts_sent = [50]
    assert limits.budget_alerts(k, 5.9) == []
    assert limits.budget_alerts(k, 8.5) == [80]
    k.alerts_sent = [50, 80]
    assert limits.budget_alerts(k, 10.0) == [100]


def test_budget_alerts_silent_without_budget():
    assert limits.budget_alerts(_key(limits={}), 999.0) == []
