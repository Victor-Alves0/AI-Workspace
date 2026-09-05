"""Testes do Remote Terminal — o que protege as duas promessas da ferramenta.

O foco não é o CRUD (o resto do app já cobre esse padrão), e sim as invariantes que,
se quebrarem, quebram em silêncio e caro:

  * o killswitch do lado do workspace nunca "tenta direto" quando o proxy falta;
  * o proxy configurado É de fato aplicado ao cliente HTTP (uma regressão aqui manda
    o tráfego pela rota normal sem nenhum erro visível);
  * segredos não vazam pela API nem pelo cache da SIFT;
  * o agente RECUSA executar quando prometeu saída selada e não conseguiu selá-la.

Sem rede: o httpx é substituído por fakes.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from aiworkspace.remote import agent_client, service
from aiworkspace.tools import sift_service


# --------------------------------------------------------------------------- #
# Killswitch e proxy do lado do workspace (camada 1)
# --------------------------------------------------------------------------- #
def _host(**over):
    base = {
        "base_url": "https://10.0.0.9:8791",
        "token": "t0ken",
        "tls_mode": "off",
        "tls_cert_pem": "",
        "proxy_url": "",
        "require_proxy": False,
    }
    base.update(over)
    return base


def test_sem_proxy_com_exigencia_recusa_em_vez_de_sair_direto():
    """A regra que dá sentido à ferramenta: proxy exigido e ausente = NÃO conecta."""
    with pytest.raises(agent_client.RemoteBlocked) as exc:
        agent_client._client(_host(require_proxy=True), 10.0)
    assert "killswitch" in str(exc.value).lower()


def test_proxy_chega_ao_cliente_http(monkeypatch):
    """Regressão silenciosa mais perigosa: o proxy salvo mas não repassado ao httpx —
    tudo funcionaria, saindo pelo IP real."""
    captured: dict = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(agent_client.httpx, "AsyncClient", FakeClient)
    agent_client._client(_host(proxy_url="socks5h://user:pw@127.0.0.1:9050",
                               require_proxy=True), 10.0)
    assert captured["proxy"] == "socks5://user:pw@127.0.0.1:9050"


def test_socks5h_vira_socks5_e_esquema_estranho_e_recusado():
    # socks5h é o que o usuário digita (DNS no proxy); no httpx isso já é o padrão
    assert agent_client.normalize_proxy("socks5h://1.2.3.4:9050") == "socks5://1.2.3.4:9050"
    assert agent_client.normalize_proxy("") == ""
    for ruim in ("ftp://1.2.3.4:21", "socks4://1.2.3.4:1080"):
        with pytest.raises(agent_client.RemoteBlocked):
            agent_client.normalize_proxy(ruim)


def test_tls_pinned_sem_certificado_recusa():
    """Melhor recusar que cair em 'sem verificação' sem o usuário saber."""
    with pytest.raises(agent_client.RemoteBlocked):
        agent_client.ssl_context("pinned", "")
    assert agent_client.ssl_context("off", "") is False


# --------------------------------------------------------------------------- #
# Segredos e normalização
# --------------------------------------------------------------------------- #
class _Row:
    """RemoteHost o suficiente para public_view (sem tocar no banco)."""
    def __init__(self, **kw):
        d = {
            "id": "11111111-1111-1111-1111-111111111111", "name": "VPS", "slug": "vps",
            "base_url": "https://10.0.0.9:8791", "enabled": True, "tls_mode": "pinned",
            "tls_cert_pem": "-----BEGIN CERTIFICATE-----x", "token": "segredo-do-agente",
            "proxy_url": "socks5h://joao:senha@127.0.0.1:9050", "require_proxy": True,
            "egress": {"mode": "force"}, "egress_proxy": "socks5h://u:p@127.0.0.1:9050",
            "workdir": "", "shell": "", "timeout_seconds": 120, "confirm_required": True,
            "status": "online", "last_error": None, "last_seen_at": None,
            "agent_version": "1.0.0", "info": {},
        }
        d.update(kw)
        for k, v in d.items():
            setattr(self, k, v)


def test_view_publica_nao_devolve_token_nem_senha_do_proxy():
    v = service.public_view(_Row())
    blob = json.dumps(v)
    assert "segredo-do-agente" not in blob
    assert "senha" not in blob
    assert v["has_token"] is True and v["has_proxy"] is True
    assert v["proxy"] == "socks5h://joao:***@127.0.0.1:9050"


def test_egress_desconhecido_cai_no_padrao_seguro():
    """Um modo inventado NÃO pode virar 'sem restrição' escondido: normaliza para off,
    que é honesto, em vez de force que mentiria sobre estar selado."""
    p = service.normalize_egress(
        {"mode": "turbo", "dns": "qualquer", "allow_hosts": ["1.2.3.4", ""]})
    assert p["mode"] == "off" and p["dns"] == "proxy"
    assert p["allow_hosts"] == ["1.2.3.4"]
    assert service.normalize_egress(None)["killswitch"] is True


def test_assinatura_da_sift_ignora_token_mas_reage_a_maquina_liberada():
    """A instância SIFT é cacheada: trocar o token NÃO deve reconstruir o índice, mas
    liberar/remover uma máquina deve — senão o gating por-modelo fica velho."""
    cfg_a = sift_service.remote_config_from_hosts("u1", [{"id": "h1", "slug": "vps"}])
    cfg_b = sift_service.remote_config_from_hosts("u1", [{"id": "h1", "slug": "vps"}])
    cfg_c = sift_service.remote_config_from_hosts("u1", [{"id": "h1"}, {"id": "h2"}])
    base = {"tool_rows": [], "search_cfg": sift_service.SearchConfig()}
    sig = lambda c: sift_service._signature(remote_cfg=c, **base)  # noqa: E731
    assert sig(cfg_a) == sig(cfg_b)
    assert sig(cfg_a) != sig(cfg_c)


def test_tool_esta_no_catalogo_como_integracao():
    paths = [t["path"] for t in sift_service.BUILTIN_TOOLS]
    assert "remote.terminal.run" in paths
    cat = sift_service.tool_category("remote.terminal.run")
    assert cat == {"category": "integration", "integration": "Remote Terminal"}


# --------------------------------------------------------------------------- #
# Agente: o killswitch do lado da máquina (camada 2)
# --------------------------------------------------------------------------- #
def _agent_module():
    """Importa o agente instalável (fora do pacote do servidor, um arquivo só)."""
    path = Path(__file__).resolve().parents[2] / "remote-agent" / "aiw_remote_agent.py"
    spec = importlib.util.spec_from_file_location("aiw_remote_agent", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["aiw_remote_agent"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def agent():
    """O agente RODA em Linux, mas IMPORTA em qualquer lugar de propósito: a política de
    saída e a decisão de recusar são a parte crítica e precisam de teste em todo CI."""
    return _agent_module()


def test_agente_recusa_comando_quando_a_politica_nao_subiu(agent):
    """`force` prometido + firewall ausente + killswitch = RECUSA.

    É o caso que decide se a ferramenta é honesta: o comando ainda RODARIA, só que sem
    proteção. Executar aqui seria vazar o IP real com a UI dizendo 'selado'."""
    ex = agent.Executor({**agent.default_config(),
                         "egress": {"mode": "force", "proxy_url": "socks5h://127.0.0.1:9050",
                                    "dns": "proxy", "killswitch": True,
                                    "allow_lan": False, "allow_hosts": []}})
    ex.fw_state = {"applied": False, "backend": "", "detail": "sem root"}
    motivo = ex.guard()
    assert motivo and "killswitch" in motivo.lower()
    assert ex.run("echo oi")["blocked"] is True


def test_agente_com_killswitch_desligado_deixa_passar(agent):
    """Desligar o killswitch é uma escolha explícita do dono — e tem que valer."""
    ex = agent.Executor({**agent.default_config(),
                         "egress": {"mode": "force", "proxy_url": "socks5h://127.0.0.1:9050",
                                    "dns": "proxy", "killswitch": False,
                                    "allow_lan": False, "allow_hosts": []}})
    ex.fw_state = {"applied": False, "backend": "", "detail": "sem root"}
    assert ex.guard() is None


def test_agente_exige_usuario_dedicado_para_selar(agent, monkeypatch):
    """Filtrar o uid 0 derrubaria a rede da máquina inteira: em vez disso, degrada e
    explica. Sem esta recusa, instalar como root deixaria o box sem rede."""
    ex = agent.Executor({**agent.default_config(),
                         "egress": {"mode": "force", "proxy_url": "socks5h://127.0.0.1:9050",
                                    "dns": "proxy", "killswitch": True,
                                    "allow_lan": False, "allow_hosts": []}})
    monkeypatch.setattr(ex, "run_uid", lambda: 0)
    monkeypatch.setattr(agent, "clear_firewall", lambda uid: None)
    st = ex.apply_policy()
    assert st["status"] == "degraded"
    assert "não-root" in st["detail"] or "root" in st["detail"]


def test_env_do_proxy_usa_socks5h_para_nao_vazar_dns(agent):
    """`socks5h` manda o NOME ao proxy. Com `socks5`, curl/git resolvem localmente e o
    DNS vaza mesmo com o tráfego tunelado — o vazamento mais fácil de não perceber."""
    env = agent.proxy_env({"proxy_url": "socks5://user:pw@127.0.0.1:9050"})
    assert env["all_proxy"].startswith("socks5h://")
    assert env["ALL_PROXY"] == env["all_proxy"]
    assert "127.0.0.1" in env["no_proxy"]


def test_regras_nft_fecham_tudo_menos_o_proxy(agent):
    """A última palavra da cadeia do uid tem que ser `reject`; sem ela a política vira
    uma lista de permissões dentro de uma porta escancarada."""
    rules = agent._nft_ruleset(1234, {"proxy_url": "socks5h://127.0.0.1:9050",
                                      "dns": "proxy", "allow_lan": False, "allow_hosts": []})
    assert "meta skuid != 1234 accept" in rules
    assert "tcp dport 9050 accept" in rules
    assert "udp dport 53" not in rules          # dns=proxy: sem DNS local
    assert rules.strip().splitlines()[-3].strip() == "reject"


def test_dns_system_libera_53_explicitamente(agent):
    rules = agent._nft_ruleset(1234, {"proxy_url": "socks5h://127.0.0.1:9050",
                                      "dns": "system", "allow_lan": True, "allow_hosts": []})
    assert "udp dport 53 accept" in rules
    assert "192.168.0.0/16" in rules
