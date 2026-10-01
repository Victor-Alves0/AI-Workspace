"""CyberLab (modo blackbox): o contrato do tool autoritativo e do protocolo.

Trava o que mantém o Mini App coerente: a IA GUIA o operador (não executa), o tool
`cyberlab` é a única fonte da verdade (escopo/fase/log/achados) e o enum de fases do
tool casa com o serviço. O fluxo gravado no banco tem teste próprio em
tests/db/test_cyberlab_live.py.
"""
from __future__ import annotations

from aiworkspace.cyberlab import service
from aiworkspace.cyberlab.protocol import protocol_for
from aiworkspace.cyberlab.turns import _CYBERLAB_TOOL, _public_state


class _FakeCase:
    mode = "blackbox"
    phase = "recon"
    target = "example.test"
    authorization = "meu laboratório"
    objective = "mapear a superfície"
    settings = {
        "log": [{"seq": i, "phase": "recon", "command": f"c{i}", "output": "o", "note": "n"} for i in range(1, 9)],
        "findings": [{"seq": 1, "title": "x", "severity": "low", "detail": "", "refs": []}],
    }


def test_protocolo_blackbox_guia_o_operador_e_nao_executa():
    p = protocol_for("blackbox").lower()
    assert "human operator" in p and "never run anything yourself" in p
    assert "scope" in p and "authorization" in p
    # modo desconhecido cai no blackbox enquanto só ele existe
    assert protocol_for("inexistente") == protocol_for("blackbox")


def test_tool_cyberlab_tem_as_acoes_e_fases_do_servico():
    fn = _CYBERLAB_TOOL["function"]
    assert fn["name"] == "cyberlab"
    props = fn["parameters"]["properties"]
    assert set(props["action"]["enum"]) == {"scope", "phase", "step", "finding", "state"}
    # o enum de fase do tool é EXATAMENTE as fases do serviço (não divergem)
    assert props["phase"]["enum"] == service.PHASES


def test_estado_publico_resume_o_caso_sem_vazar_tudo():
    st = _public_state(_FakeCase())
    assert st["phase"] == "recon" and st["target"] == "example.test"
    assert st["steps"] == 8 and len(st["recent_steps"]) == 6  # só a cauda do log
    assert st["findings"][0]["title"] == "x"
