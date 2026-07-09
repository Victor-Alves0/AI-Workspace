"""Camada de ferramentas (SIFT) — instâncias por-usuário.

Cada usuário tem uma instância SIFT própria contendo:
  - ferramentas embutidas (tempo, cálculo, web.search com a config dele)
  - as ferramentas que ELE criou (código Python registrado dinamicamente)

As instâncias são cacheadas e reconstruídas automaticamente quando as tools do
usuário mudam (assinatura) ou quando invalidadas explicitamente (ex.: troca de
segredos de busca). Tudo degrada com segurança: se a SIFT falhar, o chat segue
sem ferramentas em vez de derrubar o servidor.
"""

from __future__ import annotations

import ast
import asyncio
import datetime as _dt
import json
import logging
import math
import operator
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx

from sift import Sift
from sift.sandbox import SubprocessSandbox

from .. import deep_search, finance
from ..config import get_settings
from ..search import SearchConfig, web_search
from . import toolctx
from .sandbox import extract_valves, run_in_subprocess

logger = logging.getLogger(__name__)

# avaliador aritmético seguro (sem eval): só números e operadores
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}

# funções e constantes matemáticas liberadas na Calculadora (avaliador AST seguro,
# sem eval/import/atributos). Poderosa mas de baixíssimo custo de tokens.
_MATH_FUNCS = {
    "sqrt": math.sqrt, "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
    "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil,
    "trunc": math.trunc, "sign": lambda x: (x > 0) - (x < 0),
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "log": math.log, "log2": math.log2, "log10": math.log10, "exp": math.exp,
    "pow": math.pow, "hypot": math.hypot, "gcd": math.gcd,
    "factorial": math.factorial, "degrees": math.degrees, "radians": math.radians,
    "min": min, "max": max,
}
_MATH_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}


def _safe_eval(expr: str) -> float:
    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name) and node.id in _MATH_CONSTS:
            return _MATH_CONSTS[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            if isinstance(node.op, ast.Pow):  # limita expoentes p/ evitar DoS
                exp = _eval(node.right)
                if abs(exp) > 1000:
                    raise ValueError("expoente muito grande")
            return _BIN_OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return _UNARY_OPS[type(node.op)](_eval(node.operand))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fn = _MATH_FUNCS.get(node.func.id)
            if fn is None or node.keywords:
                raise ValueError(f"função não permitida: {getattr(node.func, 'id', '?')}")
            if node.func.id == "factorial":  # evita explosão de custo
                arg = _eval(node.args[0])
                if arg > 1000:
                    raise ValueError("fatorial muito grande")
            return fn(*[_eval(a) for a in node.args])
        raise ValueError("expressão não permitida")

    return _eval(ast.parse(expr, mode="eval").body)

# cache: user_id (str) -> (assinatura, Sift|None)
_cache: dict[str, tuple[tuple, Sift | None]] = {}


@dataclass
class GoogleConfig:
    """Config por-modelo das tools Google (Gmail + Agenda).

    O TOKEN não mora aqui (é buscado ao vivo por conta em cada chamada); só as
    contas liberadas, as operações ativas e as preferências — que definem a
    assinatura do cache SIFT. `accounts` = [{"id","email"}] (vazio = sem conta →
    responde 'não conectado'). `ops` = capacidade→ligado (ausente = ligado):
    gmail_search / gmail_send / gmail_organize / cal_view / cal_create / cal_delete."""
    user_id: str = ""
    require_confirm: bool = True     # pedir confirmação (opções) antes de escrever
    max_results: int = 10
    default_calendar: str = "primary"
    accounts: list = field(default_factory=list)
    ops: dict = field(default_factory=dict)


@dataclass
class TuyaConfig:
    """Config das tools Tuya/Smart Life. Reúne a conexão GLOBAL (creds + catálogo de
    dispositivos, de app_settings) com o gating POR-MODELO. `conn` = dict passado ao
    tuya_service (base_url, access_id, access_secret, devices, aliases, profiles,
    scenes). `allowed_devices` = nomes liberados neste modelo (vazio = todos). `ops` =
    capacidade→ligado (ausente = ligado): tuya_query / tuya_switch / tuya_ac / tuya_scene."""
    conn: dict = field(default_factory=dict)
    allowed_devices: list = field(default_factory=list)
    require_confirm: bool = True
    ops: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Ferramentas embutidas (do sistema)
# --------------------------------------------------------------------------- #
# Registro exposto p/ a UI (caixas de "Ferramentas de Sistema" no editor de modelo).
# SIFT exige paths no formato 'categoria.servico.funcao' (3 segmentos).
#   name/description => rótulo e texto da UI (idioma do app, hoje PT-BR).
#   model_desc       => linha em INGLÊS que o MODELO recebe no catálogo (modo
#                       "list"); no modo padrão "prompt" ele descobre via SIFT,
#                       que já expõe as descrições em inglês do @sift.tool.
#   O que a IA vê "por baixo dos panos" é sempre inglês (melhor desempenho); a UI
#   fica no idioma do app. Um toggle de idioma real virá depois.
BUILTIN_TOOLS: list[dict[str, str]] = [
    {"path": "utils.time.now", "name": "Data e Hora", "description": "Data/hora atuais em qualquer fuso horário.",
     "model_desc": "Current date/time in a given time zone."},
    {"path": "utils.math.eval", "name": "Calculadora", "description": "Avalia expressões com funções (sqrt, sin, log…) e constantes (pi, e).",
     "model_desc": "Evaluate a math expression (functions, constants)."},
    {"path": "user.profile.get", "name": "Perfil do Usuário", "description": "Consulta os dados da conta do usuário: nome, sobre, gênero, data de nascimento (e idade), e-mail e idioma.",
     "model_desc": "Get the user's account info: name, about, gender, birth date/age, email, language."},
    {"path": "web.search.query", "name": "Pesquisa na Web", "description": "Busca informações atuais na web (título, url, trecho).",
     "model_desc": "Search the web for current or factual info."},
    {"path": "web.page.read", "name": "Ler Página", "description": "Abre uma URL e devolve o texto legível da página (lê o conteúdo do site, não só o trecho da busca).",
     "model_desc": "Fetch a URL and return the page's readable text."},
    {"path": "diagram.excalidraw.render", "name": "Excalidraw (Diagrama)", "description": "Desenha um diagrama/fluxograma editável (canvas) a partir de Mermaid.",
     "model_desc": "Draw an editable diagram from a Mermaid flowchart."},
    {"path": "chart.render.plot", "name": "Gráfico", "description": "Desenha um gráfico (linha, barra, área ou pizza) a partir de dados.",
     "model_desc": "Render a chart (line, bar, area, pie) from data."},
    {"path": "finance.quote.get", "name": "Cotação (Ações)", "description": "Busca cotação real de ações/índices e mostra um card com mini-gráfico.",
     "model_desc": "Get a real stock/index/crypto quote as a card."},
    {"path": "research.deep.run", "name": "Deep Search", "description": "Pesquisa profunda e iterativa (planeja, busca, lê, resume) com fontes. Só quando pedida.",
     "model_desc": "Deep, multi-step web research with sources. Only when explicitly asked."},
    {"path": "automation.monitor.create", "name": "Monitor (Automação)", "description": "Cria um monitor que avisa o usuário quando algo mudar (preço, página, busca, RSS).",
     "model_desc": "Create a background monitor that alerts the user when something changes."},
    {"path": "automation.reminder.create", "name": "Lembrete (Automação)", "description": "Agenda um lembrete pontual: no horário, entrega a mensagem no chat + notificação.",
     "model_desc": "Schedule a one-shot reminder delivered in-app at a future time."},
    # Google Workspace (requer conta conectada em Integrações). Cada ferramenta
    # reúne suas operações num parâmetro `action`; a ativação de cada operação é
    # configurável na engrenagem da ferramenta.
    {"path": "google.gmail.mailbox", "name": "Google Gmail", "description": "Buscar, ler, enviar e organizar e-mails da conta Google conectada.",
     "model_desc": "Read, search, send and organize the user's Gmail."},
    {"path": "google.calendar.events", "name": "Google Calendar", "description": "Ver, buscar, criar, editar e excluir eventos da Google Agenda.",
     "model_desc": "View, search, create, edit and delete Google Calendar events."},
    # Tuya / Smart Life (requer conexão em Integrações). Controla luzes, tomadas,
    # ar-condicionado e cenas; os dispositivos e ações liberados são escolhidos na
    # engrenagem da ferramenta (por modelo).
    {"path": "smartlife.tuya.devices", "name": "Tuya Smart Home", "description": "Controlar dispositivos Smart Life/Tuya: luzes, tomadas, ar-condicionado e cenas.",
     "model_desc": "Control the user's smart home (Tuya/Smart Life): lights, plugs, AC and scenes."},
]
# NOTA: "perguntar opções" (kind:"ask") é uma PRIMITIVA de sistema (tools/interaction.py),
# não uma tool equipável — qualquer ferramenta a usa via `ask_options(...)` (ex.: o Lembrete
# quando falta o destino). Por isso não entra em BUILTIN_TOOLS.


def normalize_sift_path(path: str) -> str:
    """Garante o formato 'categoria.servico.funcao' exigido pela SIFT.

    Paths curtos criados na UI (ex.: 'previsao', 'clima.previsao') são
    completados com o prefixo 'custom' para atingir 3 segmentos, de modo que a
    ferramenta seja realmente registrada em vez de silenciosamente descartada.
    """
    parts = [p for p in (path or "").split(".") if p]
    if not parts:
        return "custom.tools.tool"
    while len(parts) < 3:
        parts.insert(0, "custom")
    return ".".join(parts)


def system_tools() -> list[dict[str, str]]:
    return BUILTIN_TOOLS


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


async def _fetch_page(url: str, max_chars: int, find: str = "") -> dict[str, Any]:
    """Baixa uma URL pública e devolve o texto legível (HTML removido).

    Reusa os guards de deep_search (`_is_public_url` anti-SSRF, `_html_to_text`).
    Nunca levanta exceção p/ fora — devolve {"error": ...}."""
    if not deep_search._is_public_url(url):
        return {"error": "URL not allowed (must be a public http/https address)"}
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            r = await client.get(
                url, headers={"User-Agent": deep_search._UA}, timeout=12
            )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"fetch failed: {exc}"}
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    ct = r.headers.get("content-type", "")
    if "html" not in ct and "text" not in ct:
        return {"error": f"unsupported content-type: {ct or 'unknown'}"}
    title = ""
    m = _TITLE_RE.search(r.text)
    if m:
        title = deep_search._html_to_text(m.group(1))[:200]
    text = deep_search._html_to_text(r.text)
    if find:
        idx = text.lower().find(find.lower())
        if idx > 0:
            text = text[max(0, idx - max_chars // 4):]
    text = text[:max_chars]
    return {"ok": True, "url": str(r.url), "title": title, "text": text, "chars": len(text)}


def _register_builtins(
    sift: Sift,
    search_cfg: SearchConfig,
    allowed: set[str] | None,
    finance_cfg: "finance.FinanceConfig | None" = None,
    deep_cfg: "deep_search.DeepSearchConfig | None" = None,
    user_id: str | None = None,
    google_cfg: "GoogleConfig | None" = None,
    tuya_cfg: "TuyaConfig | None" = None,
) -> None:
    """Registra as ferramentas de sistema. `allowed=None` = todas; caso contrário
    apenas os paths presentes no conjunto."""

    def want(path: str) -> bool:
        return allowed is None or path in allowed

    if want("utils.time.now"):
        @sift.tool(
            "utils.time.now",
            # descrição vista pelo MODELO em inglês (tool-calling mais confiável) e
            # direta: o que faz + quando usar. Nada de "retorno compacto" e afins —
            # isso é implementação, o modelo não decide nada com essa info.
            description="Current date and time in a given time zone (IANA, e.g. 'America/Sao_Paulo'; empty = UTC).",
            params={"timezone": "string:o::IANA time zone, e.g. America/Sao_Paulo (default UTC)"},
            returns=["datetime", "weekday", "unix", "error"],
        )
        def _time_now(timezone: str = "") -> dict[str, Any]:
            tz: _dt.tzinfo = _dt.timezone.utc
            if timezone:
                try:
                    from zoneinfo import ZoneInfo

                    tz = ZoneInfo(timezone)
                except Exception:  # noqa: BLE001
                    return {"error": f"invalid time zone: {timezone}"}
            now = _dt.datetime.now(tz)
            return {
                "datetime": now.strftime("%Y-%m-%d %H:%M:%S %Z").strip(),
                "weekday": now.strftime("%A"),
                "unix": int(now.timestamp()),
            }

    if want("utils.math.eval"):
        @sift.tool(
            "utils.math.eval",
            description=(
                "Evaluate a math expression: arithmetic, common functions (sqrt, sin, "
                "log, factorial, …) and constants (pi, e, tau). E.g. 'sqrt(2)*sin(pi/4)'."
            ),
            params={"expression": "string:o::math expression"},
            returns=["result", "error"],
        )
        def _math_eval(expression: str = "") -> dict[str, Any]:
            try:
                return {"result": _safe_eval(expression)}
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

    if want("user.profile.get"):
        @sift.tool(
            "user.profile.get",
            description=(
                "Get the current user's account/profile info to personalize answers or when "
                "they ask about their own data (name, age, birthday, etc.). Returns only the "
                "fields the user has filled in: name, about (free-text bio), gender, birthdate "
                "(YYYY-MM-DD) with computed age, email, language. Never invent these values — "
                "call this tool instead of guessing."
            ),
            params={},
            returns=["name", "about", "gender", "birthdate", "age", "email", "language", "note"],
        )
        def _user_profile() -> dict[str, Any]:
            prof = toolctx.user_profile.get() or {}
            out: dict[str, Any] = {k: v for k, v in prof.items() if v}
            bd = (prof.get("birthdate") or "").strip()
            if bd:
                try:
                    d = _dt.date.fromisoformat(bd)
                    today = _dt.date.today()
                    out["age"] = today.year - d.year - ((today.month, today.day) < (d.month, d.day))
                except ValueError:
                    pass
            if not out:
                out["note"] = "The user has not filled in any profile info yet."
            return out

    if want("web.search.query"):
        @sift.tool(
            "web.search.query",
            description=(
                "Search the web for current or factual info you don't know: news, "
                "schedules, sports results, prices, events."
            ),
            params={
                "query": "string:o::search terms",
                "limit": "number:o:5:max results (1-10)",
            },
            returns=["results", "error"],
        )
        def _web_search(query: str = "", limit: int = 5) -> dict[str, Any]:
            try:
                n = max(1, min(int(limit or 5), 10))
                results = asyncio.run(web_search(query, search_cfg))
                # enxuga: trecho curto, sem campos redundantes → poucos tokens
                trimmed = [
                    {
                        "title": (r.get("title") or "")[:120],
                        "url": r.get("url") or "",
                        "snippet": (r.get("content") or "").strip()[:200],
                    }
                    for r in results[:n]
                ]
                return {"results": trimmed}
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

    if want("web.page.read"):
        @sift.tool(
            "web.page.read",
            description=(
                "Fetch a web page by URL and return its readable text (HTML stripped). "
                "Use this to actually READ a page's content — e.g. after web.search.query "
                "gives you a link, or when the user hands you a URL. Public http/https "
                "pages only. Optionally pass `find` to center the excerpt on a keyword."
            ),
            params={
                "url": "string:o::the page URL (http/https)",
                "max_chars": "number:o:6000:max characters of text to return (500-20000)",
                "find": "string:o::optional keyword to focus the excerpt around",
            },
            returns=["ok", "url", "title", "text", "chars", "error"],
        )
        def _web_page_read(url: str = "", max_chars: Any = 6000, find: str = "") -> dict[str, Any]:
            u = (url or "").strip()
            if not u:
                return {"error": "provide a URL (http/https)"}
            if not u.lower().startswith(("http://", "https://")):
                u = "https://" + u
            try:
                cap = max(500, min(int(max_chars or 6000), 20000))
            except (TypeError, ValueError):
                cap = 6000
            try:
                return asyncio.run(_fetch_page(u, cap, (find or "").strip()))
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

    if want("diagram.excalidraw.render"):
        @sift.tool(
            "diagram.excalidraw.render",
            description=(
                "Draw an editable diagram (Excalidraw canvas) from a Mermaid flowchart — "
                "flowcharts, architectures, mind maps. Shown to the user; don't redraw it "
                "as text. Optionally write [[diagram]] where it should appear."
            ),
            params={
                "mermaid": "string:o::Mermaid flowchart, e.g. 'flowchart TD\\n A[Start]-->B{Ok?}\\n B-->|Yes| C[End]'",
                "title": "string:o::diagram title (optional)",
            },
            # `kind`+`mermaid` precisam sobreviver ao filtro do SIFT: o front lê
            # esse resultado (tool_events) p/ renderizar o canvas.
            returns=["ok", "kind", "title", "mermaid", "error"],
        )
        def _excalidraw(mermaid: str = "", title: str = "") -> dict[str, Any]:
            src = (mermaid or "").strip()
            # remove cercas de código ```mermaid ... ``` se o modelo as enviar
            if src.startswith("```"):
                src = src.split("\n", 1)[1] if "\n" in src else ""
                if src.rstrip().endswith("```"):
                    src = src.rstrip()[:-3]
                src = src.strip()
            if not src:
                return {"error": "provide `mermaid` (e.g. 'flowchart TD\\n A-->B')"}
            # retorno compacto: só o necessário p/ o front renderizar o canvas
            # (NUNCA o HTML/SVG — economia de tokens, princípio do SIFT)
            return {
                "ok": True,
                "kind": "excalidraw",
                "title": (title or "Diagrama").strip()[:120],
                "mermaid": src[:6000],
            }

    if want("chart.render.plot"):
        @sift.tool(
            "chart.render.plot",
            description=(
                "Render a chart (line, bar, area, pie) to visualize numbers, comparisons "
                "or trends. Use `labels`+`values` for one series or `series` for many. "
                "Optionally write [[chart]] where it should appear."
            ),
            params={
                "type": "string:o:line:chart type: line, bar, area or pie",
                "title": "string:o::chart title",
                "labels": "array:o::x-axis labels, e.g. ['Jan','Feb','Mar']",
                "values": "array:o::numeric values aligned to labels, e.g. [10, 20, 15]",
                "series": "array:o::multiple series: [{name, data:[...]}, ...]",
            },
            returns=["kind", "type", "title", "labels", "series", "error"],
        )
        def _chart(type: str = "line", title: str = "", labels: Any = None,
                   values: Any = None, series: Any = None) -> dict[str, Any]:
            def _num(v: Any) -> float:
                try:
                    return round(float(v), 6)
                except (TypeError, ValueError):
                    return 0.0
            t = (type or "line").lower()
            if t not in ("line", "bar", "area", "pie"):
                t = "line"
            labs = [str(x) for x in (labels or [])]
            norm: list[dict[str, Any]] = []
            if isinstance(series, list) and series:
                for s in series:
                    if isinstance(s, dict) and isinstance(s.get("data"), list):
                        norm.append({"name": str(s.get("name") or ""), "data": [_num(v) for v in s["data"]]})
            elif isinstance(values, list) and values:
                norm.append({"name": str(title or "série"), "data": [_num(v) for v in values]})
            if not norm or not any(s["data"] for s in norm):
                return {"error": "provide `values` (numbers) or `series`, plus optional `labels`"}
            return {"kind": "chart", "type": t, "title": (title or "")[:120], "labels": labs, "series": norm}

    if want("finance.quote.get"):
        # config default + injeta a busca web p/ o fallback (usa a mesma search_cfg)
        fin_cfg = finance_cfg or finance.FinanceConfig()

        async def _fin_ws(q: str) -> list[dict]:
            return await web_search(q, search_cfg)

        fin_cfg.web_search = _fin_ws

        @sift.tool(
            "finance.quote.get",
            description=(
                "Get a real stock/index/ETF/crypto quote (e.g. AAPL, NESN.SW, PETR4.SA, "
                "BTC-USD). By DEFAULT return only the numbers and present them as a "
                "text/table. ONLY when the user asks for a chart/card/visual (e.g. 'com "
                "gráfico', 'mostra o card', 'with a chart'), pass chart=true to render a "
                "visual card with a mini price chart, and write [[stock]] where it should "
                "appear. Pass chart=false to force text."
            ),
            params={
                "symbol": "string:o::ticker symbol, e.g. AAPL, NESN.SW, PETR4.SA",
                "range": "string:o:1d:period: 1d, 5d, 1mo, 6mo, ytd, 1y, 5y, max",
                "chart": "boolean:o::true = show the visual card + mini chart (only if the user asks for a chart/card); omit or false = numbers only",
            },
            returns=[
                "kind", "ok", "symbol", "name", "exchange", "currency", "price",
                "prev_close", "change", "change_pct", "series", "stats", "source",
                "range", "web_results", "error",
            ],
        )
        def _finance_quote(symbol: str = "", range: str = "1d", chart: Any = None) -> dict[str, Any]:
            try:
                q = asyncio.run(finance.fetch_quote(symbol, fin_cfg, range or "1d"))
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}
            if q.get("error"):
                return q
            q["ok"] = True
            # `chart` não especificado => usa o modo configurado no modelo
            # (fin_cfg.default_card, padrão False = texto). Explícito vence.
            if chart is None or (isinstance(chart, str) and not chart.strip()):
                want_chart = bool(fin_cfg.default_card)
            else:
                want_chart = not (chart is False or str(chart).strip().lower() in ("false", "0", "no", "off", "nao", "não"))
            if want_chart:
                q["kind"] = "stock_card"  # o front desenha o card com mini-gráfico
            else:
                q.pop("series", None)      # só os números (o modelo formata em texto)
            return q

    if want("research.deep.run"):
        d_cfg = deep_cfg or deep_search.DeepSearchConfig()

        async def _deep_ws(qq: str) -> list[dict]:
            return await web_search(qq, search_cfg)

        d_cfg.web_search = _deep_ws

        @sift.tool(
            "research.deep.run",
            description=(
                "Deep, multi-step web research: plans sub-questions, searches, reads pages, "
                "returns a synthesized brief WITH sources. Slow and costly — use ONLY when the "
                "user EXPLICITLY asks for it ('pesquisa profunda', 'deep research', 'investigue "
                "a fundo'). Needing a web lookup is NOT enough: questions about a link/product/"
                "fact use page.read and web.search instead. Optionally write [[research]] where "
                "it should appear. Always follow it with your own textual answer to the user."
            ),
            params={"query": "string:o::the research topic or question"},
            returns=["kind", "query", "brief", "sources", "rounds", "error"],
        )
        def _deep(query: str = "") -> dict[str, Any]:
            try:
                return asyncio.run(deep_search.run(query, d_cfg))
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

    if want("automation.monitor.create"):
        @sift.tool(
            "automation.monitor.create",
            description=(
                "Create a background monitor that alerts the user when something changes. "
                "Use ONLY when the user asks to be notified/alerted about a future event "
                "(e.g. 'tell me when…', 'alert me if the price hits…'). Pick watcher_type: "
                "'price' (needs symbol, op=above|below|pct, value), 'web_search' (needs query; "
                "optional condition), 'page' (needs url; optional contains), 'rss' (needs url; "
                "optional keywords). The monitor then runs on its own and notifies the user."
            ),
            params={
                "watcher_type": "string:o:price:one of: price, web_search, page, rss",
                "title": "string:o::short title for the monitor",
                "symbol": "string:o::ticker for price, e.g. PETR4.SA, AAPL, BTC-USD",
                "op": "string:o:above:price condition: above, below or pct",
                "value": "number:o::threshold (price) or percent (pct)",
                "query": "string:o::search query for web_search",
                "condition": "string:o::natural-language condition for web_search",
                "url": "string:o::page or feed URL for page/rss",
                "contains": "string:o::text to watch on the page (page)",
                "keywords": "string:o::comma-separated keywords (rss)",
                "interval_minutes": "number:o:5:how often to check, in minutes",
            },
            returns=["ok", "monitor_id", "title", "watcher_type", "error"],
        )
        def _monitor_create(
            watcher_type: str = "price", title: str = "", symbol: str = "",
            op: str = "above", value: Any = None, query: str = "", condition: str = "",
            url: str = "", contains: str = "", keywords: str = "", interval_minutes: Any = 5,
        ) -> dict[str, Any]:
            if not user_id:
                return {"error": "monitor creation unavailable (no user context)"}
            # import tardio: evita ciclo com o pacote automation em tempo de import
            from ..automation import creator
            try:
                return asyncio.run(creator.create_monitor(
                    user_id, title=title, watcher_type=watcher_type,
                    interval_minutes=interval_minutes, symbol=symbol, op=op, value=value,
                    query=query, condition=condition, url=url, contains=contains, keywords=keywords,
                ))
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

    if want("automation.reminder.create"):
        @sift.tool(
            "automation.reminder.create",
            description=(
                "Schedule a ONE-SHOT in-app reminder for the user at a future time. Use when "
                "the user asks to be reminded/pinged later (e.g. 'remind me to sleep in 2 "
                "minutes', 'me lembra às 14:00'). Set `in_minutes` for a relative delay OR "
                "`at` for a clock time ('HH:MM' in the USER'S LOCAL timezone) — get the current "
                "local time from the temporal context. `message` is the exact text to send. "
                "This delivers IN-APP (chat + notification). If the user wants it on their "
                "AGENDA/calendar instead, use the Google Calendar tool. If the user did NOT say "
                "where to receive it, OMIT `target`: this returns a quick-pick selector — just "
                "show it and end your turn. Only set target='current' (this chat) or 'new' (a "
                "fresh chat) when the user already made it clear."
            ),
            params={
                "message": "string:o::the reminder text to deliver, e.g. 'lembre-se de dormir'",
                "in_minutes": "number:o::delay from now in minutes (use this for 'in X min/hours')",
                "at": "string:o::local clock time 'HH:MM' or 'YYYY-MM-DD HH:MM' (user's timezone); alternative to in_minutes",
                "target": "string:o::where to deliver: 'current' (this chat) or 'new'; OMIT to let the user pick",
                "title": "string:o::short title for the reminder (optional)",
            },
            returns=["ok", "reminder_id", "title", "when", "kind", "question", "options", "allow_custom", "custom_label", "error"],
        )
        def _reminder_create(
            message: str = "", in_minutes: Any = None, at: str = "",
            target: str = "", title: str = "",
        ) -> dict[str, Any]:
            if not user_id:
                return {"error": "reminder creation unavailable (no user context)"}
            # sem destino explícito → pergunta ao usuário (opções de acesso rápido)
            if not (target or "").strip():
                if not (message or "").strip():
                    return {"error": "reminder requires a `message`"}
                from .interaction import ask_options
                return ask_options(
                    "Onde você quer receber esse lembrete?",
                    [
                        {"label": "Na minha agenda (Google Calendar)",
                         "value": "Coloque na minha agenda do Google Calendar como um evento (use a ferramenta de calendário; se não estiver conectada, me avise)."},
                        {"label": "Aqui neste chat", "value": "Manda o lembrete aqui neste mesmo chat"},
                        {"label": "Em um novo chat", "value": "Manda o lembrete em um chat novo"},
                    ],
                    allow_custom=False,
                )
            from ..automation import creator
            chat_id = toolctx.current_chat_id.get()
            try:
                return asyncio.run(creator.create_reminder(
                    user_id, message=message, in_minutes=in_minutes, at=at,
                    target=target, chat_id=chat_id, title=title,
                    tz=toolctx.user_tz.get(),
                ))
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

    # ---------------------- Google Workspace (Gmail + Agenda) ------------------ #
    # Paths de 3 segmentos (exigência da SIFT); os nomes na UI são "Google Gmail" /
    # "Google Calendar". Cada tool reúne suas operações no parâmetro `action`.
    if want("google.gmail.mailbox") or want("google.calendar.events"):
        g_confirm = True if google_cfg is None else bool(google_cfg.require_confirm)
        g_max = (google_cfg.max_results if google_cfg else 10) or 10
        g_cal = (google_cfg.default_calendar if google_cfg else "primary") or "primary"
        g_accounts = list(google_cfg.accounts) if google_cfg else []  # [{"id","email"}]
        g_ops = google_cfg.ops if google_cfg else {}

        def _op_on(cap: str) -> bool:
            """Operação ligada na config da ferramenta (ausente = ligada)."""
            return g_ops.get(cap, True) is not False

        _ASK_KEYS = ["kind", "question", "options", "allow_custom", "custom_label"]
        _ACCOUNT_PARAM = "string:o::which connected Google account to use (email); omit if only one"

        def _g_truthy(v: Any) -> bool:
            if v is True:
                return True
            return isinstance(v, str) and v.strip().lower() in ("true", "1", "yes", "sim", "on")

        def _g_pick_account(account: str = "") -> tuple[dict | None, dict | None]:
            """Escolhe a conta (dict {id,email}) sem buscar token. Retorna (conta,
            bloqueio): bloqueio != None = não conectado / escolha de conta / erro."""
            if not g_accounts:
                return None, {"error": "Google não conectado. Conecte uma conta em Configurações → Integrações."}
            if (account or "").strip():
                a = account.strip().lower()
                chosen = next(
                    (x for x in g_accounts
                     if a == str(x.get("id", "")).lower() or a == (x.get("email", "") or "").lower()),
                    None,
                )
                if chosen is None:
                    emails = ", ".join(x.get("email", "") for x in g_accounts)
                    return None, {"error": f"conta '{account}' não liberada para este modelo. Disponíveis: {emails}"}
                return chosen, None
            if len(g_accounts) == 1:
                return g_accounts[0], None
            from .interaction import ask_options
            return None, ask_options(
                "Qual conta Google devo usar?",
                [{"label": x.get("email", ""), "value": f"Use a conta {x.get('email', '')}"} for x in g_accounts],
                allow_custom=False,
            )

        def _g_ctx(account: str = "") -> tuple[str | None, dict | None]:
            """Resolve a conta e devolve (access_token, bloqueio)."""
            chosen, block = _g_pick_account(account)
            if block is not None:
                return None, block
            from ..integrations import google_service
            tok = asyncio.run(google_service.get_access_token(str(chosen.get("id"))))
            if not tok:
                return None, {"error": f"não foi possível acessar a conta {chosen.get('email', '')} (reconecte em Integrações)."}
            return tok, None

        def _g_guard(summary: str, confirm: Any) -> dict | None:
            """Confirmação antes de escrever. Retorna ask, ou None p/ prosseguir.
            (O bloqueio por operação é feito no `_op_on` de cada ação.)"""
            if g_confirm and not _g_truthy(confirm):
                from .interaction import ask_options
                return ask_options(
                    summary,
                    [
                        {"label": "Confirmar", "value": "Sim, confirmo — refaça a ação agora com confirm=true."},
                        {"label": "Cancelar", "value": "Cancele, não execute a ação."},
                    ],
                    allow_custom=False,
                )
            return None

        def _g_n(max_results: Any) -> int:
            return min(int(max_results or g_max), 50) if str(max_results or "").strip() else g_max

        def _g_search_n(max_results: Any) -> int:
            """Nº de e-mails da BUSCA do Gmail. Omitido → default do usuário; `0`
            (ou negativo) → TODOS que casam a query (o google_service aplica o teto
            de segurança). Diferente do `_g_n` do calendário, aqui 0 NÃO vira default."""
            s = str(max_results if max_results is not None else "").strip()
            if s == "":
                return g_max
            return max(0, int(float(s)))

        if want("google.gmail.mailbox"):
            @sift.tool(
                "google.gmail.mailbox",
                description=(
                    "The user's Gmail. `action`: 'search' (list messages; `query` uses Gmail "
                    "operators like from:/is:unread/newer_than:7d), 'read' (full body by `id`), "
                    "'send' (needs `to`,`subject`,`body`), 'archive'/'trash'/'mark_read'/"
                    "'mark_unread' (by `id`). For 'send', just provide the fields: the user is "
                    "shown an EDITABLE draft to review and send themselves — do NOT say you sent "
                    "it, and don't ask for confirmation in text (the draft IS the confirmation)."
                ),
                params={
                    "action": "string:n::search | read | send | archive | trash | mark_read | mark_unread",
                    "query": "string:o::search: Gmail query (operators allowed)",
                    "id": "string:o::read/archive/trash/mark: the message id",
                    "to": "string:o::send: recipient(s), comma-separated",
                    "subject": "string:o::send: subject",
                    "body": "string:o::send: plain-text body",
                    "cc": "string:o::send: optional CC",
                    "max_results": "number:o::search: max messages (metadata; capped). To just COUNT matches (e.g. 'how many unread?'), pass 0 — fast, returns `count` + a small sample. Omit for the default",
                    "confirm": "boolean:o::set true only after the user confirmed a write (archive/trash/mark)",
                    "account": _ACCOUNT_PARAM,
                },
                returns=["messages", "count", "note", "from", "subject", "date", "snippet", "body", "id", "ok", "action", "error",
                         "kind", "draft_id", "to", "cc", "subject", "body", "account", "account_email", *_ASK_KEYS],
                risk=True,
                examples=["read my last email", "any unread emails?", "send an email to bob", "archive this message"],
            )
            def _g_gmail(action: str = "", query: str = "", id: str = "", to: str = "",
                         subject: str = "", body: str = "", cc: str = "", max_results: Any = None,
                         confirm: Any = None, account: str = "") -> dict[str, Any]:
                act = (action or "").strip().lower()
                reads = {"search", "read"}
                writes = {"send", "archive", "trash", "mark_read", "mark_unread"}
                if act not in reads | writes:
                    return {"error": f"unknown action '{action}' (use search/read/send/archive/trash/mark_read/mark_unread)"}
                cap = ("gmail_send" if act == "send"
                       else "gmail_organize" if act in {"archive", "trash", "mark_read", "mark_unread"}
                       else "gmail_search")
                if not _op_on(cap):
                    return {"error": f"a operação '{act}' está desativada nas configurações desta ferramenta."}
                # ENVIO: com confirmação ligada e usuário presente (não é automação),
                # devolve um RASCUNHO editável — o composer no chat é a confirmação.
                # A IA não envia; o usuário revisa, edita e clica Enviar (rota direta).
                if act == "send":
                    if not (to or "").strip():
                        return {"error": "`to` is required for send"}
                    if g_confirm and not toolctx.background.get():
                        chosen, block = _g_pick_account(account)
                        if block is not None:
                            return block
                        import uuid as _uuid
                        return {
                            "kind": "email_draft",
                            "draft_id": str(_uuid.uuid4()),
                            "to": to, "cc": cc, "subject": subject, "body": body,
                            "account": str(chosen.get("id")),
                            "account_email": chosen.get("email", ""),
                        }
                    # confirmação desligada OU automação → envia direto (abaixo)
                elif act in writes:
                    blocked = _g_guard(f"{act} no e-mail selecionado?", confirm)
                    if blocked is not None:
                        return blocked
                tok, block = _g_ctx(account)
                if block is not None:
                    return block
                from ..integrations import google_service
                try:
                    if act == "search":
                        return google_service.gmail_search(tok, query, _g_search_n(max_results))
                    if act == "read":
                        if not (id or "").strip():
                            return {"error": "`id` is required for read"}
                        return google_service.gmail_get(tok, id)
                    if act == "send":
                        if not (to or "").strip():
                            return {"error": "`to` is required for send"}
                        return google_service.gmail_send(tok, to, subject, body, cc)
                    if not (id or "").strip():
                        return {"error": "`id` is required"}
                    return google_service.gmail_modify(
                        tok, id,
                        {"archive": "archive", "trash": "trash", "mark_read": "read", "mark_unread": "unread"}[act],
                    )
                except Exception as exc:  # noqa: BLE001
                    return {"error": str(exc)}

        if want("google.calendar.events"):
            @sift.tool(
                "google.calendar.events",
                description=(
                    "The user's Google Calendar. Use it to put reminders/events on the user's "
                    "agenda. `action`: 'list' (upcoming events; `time_min`/`time_max` RFC3339), "
                    "'search' (`query` text), 'create' (needs `summary`,`start`,`end`; RFC3339 "
                    "datetime or YYYY-MM-DD for all-day), 'update' (`event_id` + fields to change), "
                    "'delete' (`event_id`). Give `start`/`end` as the user's LOCAL wall-clock time "
                    "(from the temporal context) WITHOUT a UTC offset, e.g. '2026-07-07T14:30:00' — "
                    "the server tags it with the user's timezone. Writes ask for confirmation "
                    "unless confirm=true."
                ),
                params={
                    "action": "string:n::list | search | create | update | delete",
                    "query": "string:o::search: text",
                    "time_min": "string:o::list: RFC3339 lower bound (default now)",
                    "time_max": "string:o::list: RFC3339 upper bound",
                    "summary": "string:o::create/update: event title",
                    "start": "string:o::create/update: local RFC3339 datetime (no offset) or YYYY-MM-DD",
                    "end": "string:o::create/update: local RFC3339 datetime (no offset) or YYYY-MM-DD",
                    "description": "string:o::create/update: details",
                    "location": "string:o::create/update: location",
                    "attendees": "string:o::create: attendee emails, comma-separated",
                    "event_id": "string:o::update/delete: the event id",
                    "max_results": "number:o::list/search: max events (1-50)",
                    "calendar_id": "string:o::calendar id (default primary)",
                    "confirm": "boolean:o::set true only after the user confirmed a write",
                    "account": _ACCOUNT_PARAM,
                },
                returns=["events", "id", "title", "start", "end", "location", "ok", "deleted", "action", "error", *_ASK_KEYS],
                risk=True,
                examples=["what's on my calendar tomorrow", "schedule a meeting friday 3pm", "delete that event", "reschedule my dentist appointment"],
            )
            def _g_calendar(action: str = "", query: str = "", time_min: str = "", time_max: str = "",
                            summary: str = "", start: str = "", end: str = "", description: str = "",
                            location: str = "", attendees: str = "", event_id: str = "",
                            max_results: Any = None, calendar_id: str = "", confirm: Any = None,
                            account: str = "") -> dict[str, Any]:
                act = (action or "").strip().lower()
                reads = {"list", "search"}
                writes = {"create", "update", "delete"}
                if act not in reads | writes:
                    return {"error": f"unknown action '{action}' (use list/search/create/update/delete)"}
                cap = "cal_view" if act in reads else "cal_delete" if act == "delete" else "cal_create"
                if not _op_on(cap):
                    return {"error": f"a operação '{act}' está desativada nas configurações desta ferramenta."}
                if act in writes:
                    summary_txt = ("Excluir este evento da agenda?" if act == "delete"
                                   else f"Criar evento “{summary}” ({start} → {end})?" if act == "create"
                                   else "Salvar as alterações neste evento?")
                    blocked = _g_guard(summary_txt, confirm)
                    if blocked is not None:
                        return blocked
                tok, block = _g_ctx(account)
                if block is not None:
                    return block
                from ..integrations import google_service
                cal = calendar_id or g_cal
                tz = toolctx.user_tz.get()  # fuso do usuário → horas locais na agenda
                try:
                    if act == "list":
                        tmin = time_min or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                        return google_service.cal_list(tok, tmin, time_max, _g_n(max_results), cal)
                    if act == "search":
                        return google_service.cal_search(tok, query, _g_n(max_results), cal)
                    if act == "create":
                        if not ((summary or "").strip() and (start or "").strip() and (end or "").strip()):
                            return {"error": "`summary`, `start` and `end` are required"}
                        return google_service.cal_create(tok, summary, start, end, description, location, attendees, cal, tz)
                    if act == "update":
                        if not (event_id or "").strip():
                            return {"error": "`event_id` is required"}
                        return google_service.cal_update(tok, event_id, summary, start, end, description, location, cal, tz)
                    if not (event_id or "").strip():
                        return {"error": "`event_id` is required"}
                    return google_service.cal_delete(tok, event_id, cal)
                except Exception as exc:  # noqa: BLE001
                    return {"error": str(exc)}

    # ---------------------- Tuya / Smart Life (casa) --------------------------- #
    if want("smartlife.tuya.devices"):
        t_conn = tuya_cfg.conn if tuya_cfg else {}
        t_confirm = True if tuya_cfg is None else bool(tuya_cfg.require_confirm)
        t_allowed = {str(d) for d in (tuya_cfg.allowed_devices if tuya_cfg else [])}
        t_ops = tuya_cfg.ops if tuya_cfg else {}

        def _t_on(cap: str) -> bool:
            return t_ops.get(cap, True) is not False

        def _t_truthy(v: Any) -> bool:
            if v is True:
                return True
            return isinstance(v, str) and v.strip().lower() in ("true", "1", "yes", "sim", "on")

        def _t_allowed(device: str) -> dict | None:
            """Bloqueio se o device (resolvido) não está liberado neste modelo."""
            if not t_allowed:
                return None
            from ..integrations import tuya_service
            resolved = tuya_service.resolve_device(t_conn, device)
            name = resolved["name"] if resolved else device
            if name in t_allowed:
                return None
            return {"error": f"o dispositivo '{name}' não está liberado para este modelo."}

        def _t_guard(summary: str, confirm: Any) -> dict | None:
            if t_confirm and not _t_truthy(confirm):
                from .interaction import ask_options
                return ask_options(
                    summary,
                    [
                        {"label": "Confirmar", "value": "Sim, confirmo — refaça a ação agora com confirm=true."},
                        {"label": "Cancelar", "value": "Cancele, não execute a ação."},
                    ],
                    allow_custom=False,
                )
            return None

        @sift.tool(
            "smartlife.tuya.devices",
            description=(
                "Control the user's smart home (Tuya / Smart Life) — lights (luz, lâmpada), "
                "plugs/sockets (tomada), air conditioner (ar-condicionado), projector (projetor), "
                "scenes (cena). Use this for ANY request to turn devices on/off or control the "
                "home, including Portuguese phrasings like 'apague a luz do quarto', 'liga o ar', "
                "'acende a lâmpada'. `action`: 'list' (show the user's devices/scenes — call this "
                "first if unsure what exists), 'status' (a device's live state: on/off, and for an "
                "AC its temperature/mode/fan), 'on'/'off' (switch a light/plug/device by `device`; "
                "optional `channel`), 'ac' (turn on and set the AC: `temperature`, `mode` "
                "cold/hot/auto/dry/fan, `fan` auto/low/mid/high; use action 'off' to turn it off), "
                "'scene' (trigger a tap-to-run `scene`). `device` accepts a name or natural alias. "
                "Writes ask for confirmation unless confirm=true."
            ),
            params={
                "action": "string:n::list | status | on | off | ac | scene",
                "device": "string:o::the device name or alias (on/off/status/ac)",
                "channel": "number:o:1:on/off: switch channel (1, or 2 for a second button)",
                "temperature": "number:o:23:ac: target temperature in Celsius",
                "mode": "string:o:frio:ac: cold/hot/auto/dry/fan (aliases accepted)",
                "fan": "string:o:auto:ac: fan speed auto/low/mid/high",
                "scene": "string:o::scene: the scene/tap-to-run name",
                "confirm": "boolean:o::set true only after the user confirmed the action",
            },
            returns=["ok", "devices", "aliases", "scenes", "device", "status", "functions",
                     "action", "temperature", "mode", "fan", "scene", "error",
                     "kind", "question", "options", "allow_custom", "custom_label"],
            risk=True,
            examples=["apague a luz do quarto", "acenda a lâmpada da sala", "liga a luz",
                      "desliga o ar", "liga o ar condicionado a 22 graus", "liga a tomada",
                      "ligar o projetor", "quais dispositivos eu tenho", "ativar uma cena",
                      "turn on the bedroom light", "turn off the AC", "what devices do I have"],
        )
        def _tuya(action: str = "", device: str = "", channel: Any = 1,
                  temperature: Any = 23, mode: str = "frio", fan: str = "auto",
                  scene: str = "", confirm: Any = None) -> dict[str, Any]:
            act = (action or "").strip().lower()
            if not t_conn:
                return {"error": "Tuya não conectado. Configure em Configurações → Integrações."}
            reads = {"list", "status"}
            writes = {"on", "off", "ac", "scene"}
            if act not in reads | writes:
                return {"error": f"unknown action '{action}' (use list/status/on/off/ac/scene)"}
            cap = ("tuya_query" if act in reads else "tuya_ac" if act == "ac"
                   else "tuya_scene" if act == "scene" else "tuya_switch")
            if not _t_on(cap):
                return {"error": f"a operação '{act}' está desativada nas configurações desta ferramenta."}
            from ..integrations import tuya_service
            try:
                if act == "list":
                    return tuya_service.list_devices(t_conn)
                if act == "status":
                    if not (device or "").strip():
                        return {"error": "`device` is required for status"}
                    blocked = _t_allowed(device)
                    return blocked if blocked else tuya_service.device_state(t_conn, device)
                # writes
                if act in {"on", "off"} and not (device or "").strip():
                    return {"error": "`device` is required"}
                if act == "scene" and not (scene or "").strip():
                    return {"error": "`scene` is required"}
                if act != "scene":  # on/off/ac miram um device (ac default = "ar")
                    blocked = _t_allowed(device or "ar")
                    if blocked:
                        return blocked
                summary = {
                    "on": f"Ligar '{device}'?",
                    "off": f"Desligar '{device}'?",
                    "ac": f"Ligar o ar '{device or 'ar'}' em {temperature}°C ({mode})?",
                    "scene": f"Disparar a cena '{scene}'?",
                }[act]
                blocked = _t_guard(summary, confirm)
                if blocked is not None:
                    return blocked
                try:
                    ch = int(channel or 1)
                except (TypeError, ValueError):
                    ch = 1
                if act == "on":
                    return tuya_service.set_switch(t_conn, device, True, ch)
                if act == "off":
                    return tuya_service.set_switch(t_conn, device, False, ch)
                if act == "ac":
                    try:
                        temp = int(float(temperature or 23))
                    except (TypeError, ValueError):
                        temp = 23
                    return tuya_service.configure_ac(t_conn, temp, mode or "frio", fan or "auto", device or "ar")
                return tuya_service.trigger_scene(t_conn, scene)
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}


# --------------------------------------------------------------------------- #
# Ferramentas do usuário (código dinâmico)
# --------------------------------------------------------------------------- #
def _register_user_tool(sift: Sift, *, path: str, description: str, params: dict,
                        returns: list, code: str, valves: dict) -> None:
    """Registra a tool do usuário. O código NÃO roda aqui: a cada chamada ele é
    executado em subprocesso isolado (sandbox), com timeout e limites de recurso.

    Contrato: o código deve definir `def run(**params): ...` retornando algo
    serializável em JSON. As `valves` configuradas ficam disponíveis como global.
    """

    # garante que "error" sobreviva ao filtro de resposta do SIFT: senão, quando a
    # tool falha, o modelo recebe {} (a mensagem de erro é descartada pelo returns).
    returns = list(returns or [])
    if "error" not in returns:
        returns = returns + ["error"]

    @sift.tool(normalize_sift_path(path), description=description, params=params, returns=returns)
    def _wrapper(**kwargs):  # noqa: ANN003
        try:
            return run_in_subprocess(code, kwargs, valves)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}


# --------------------------------------------------------------------------- #
# Construção / cache
# --------------------------------------------------------------------------- #
def _signature(
    tool_rows: Iterable[Any], search_cfg: SearchConfig,
    finance_cfg: "finance.FinanceConfig | None" = None,
    deep_cfg: "deep_search.DeepSearchConfig | None" = None,
    google_cfg: "GoogleConfig | None" = None,
    tuya_cfg: "TuyaConfig | None" = None,
) -> tuple:
    rows = tuple(
        sorted(
            (str(t.id), t.path, t.enabled, t.updated_at.isoformat())
            for t in tool_rows
        )
    )
    fin = (
        (tuple(finance_cfg.providers), finance_cfg.web_fallback,
         bool(finance_cfg.finnhub_key), bool(finance_cfg.alphavantage_key),
         finance_cfg.default_card)
        if finance_cfg else ()
    )
    dp = (
        (deep_cfg.model, deep_cfg.max_subqueries, deep_cfg.max_rounds,
         deep_cfg.read_pages, deep_cfg.max_pages, bool(deep_cfg.api_key))
        if deep_cfg else ()
    )
    # prefs + contas liberadas entram na assinatura (o token NÃO): muda
    # config/conta → rebuild; refresh de token → sem rebuild.
    gg = (
        (google_cfg.require_confirm, google_cfg.max_results, google_cfg.default_calendar,
         tuple(sorted(google_cfg.ops.items())),
         tuple(sorted(str(a.get("id")) for a in google_cfg.accounts)))
        if google_cfg else ()
    )
    # config Tuya: conexão global (id + versão do catálogo) + gating por-modelo.
    # o access_secret não entra (só um bool); o catálogo entra por um hash barato.
    ty = (
        (tuya_cfg.conn.get("access_id", ""), tuya_cfg.conn.get("base_url", ""),
         bool(tuya_cfg.conn.get("access_secret")),
         hash(json.dumps(
             {k: tuya_cfg.conn.get(k) for k in ("devices", "aliases", "scenes")},
             sort_keys=True, default=str,
         )),
         tuya_cfg.require_confirm, tuple(sorted(tuya_cfg.ops.items())),
         tuple(sorted(str(d) for d in tuya_cfg.allowed_devices)))
        if tuya_cfg else ()
    )
    return (
        rows,
        search_cfg.provider,
        search_cfg.searxng_url,
        tuple(search_cfg.providers),
        search_cfg.multi,
        search_cfg.max_results,
        tuple(search_cfg.domain_filter),
        fin,
        dp,
        gg,
        ty,
    )


def _index_cache_path(user_id: str | None) -> str | None:
    """Arquivo .npz de cache do índice p/ o usuário (None = desabilitado)."""
    cache_dir = get_settings().sift_index_cache_dir
    if not cache_dir or not user_id:
        return None
    try:
        os.makedirs(cache_dir, exist_ok=True)
        return os.path.join(cache_dir, f"{user_id}.npz")
    except OSError as exc:
        logger.warning("Cache de índice SIFT indisponível (%s); seguindo sem", exc)
        return None


def build_user_sift(
    tool_rows: Iterable[Any], search_cfg: SearchConfig, user_id: str | None = None,
    finance_cfg: "finance.FinanceConfig | None" = None,
    deep_cfg: "deep_search.DeepSearchConfig | None" = None,
    google_cfg: "GoogleConfig | None" = None,
    tuya_cfg: "TuyaConfig | None" = None,
) -> Sift | None:
    """Constrói a instância SIFT completa do usuário (builtins + tools dele).

    O gating por modelo NÃO acontece aqui: a instância contém tudo e o chamador
    aplica `sift.scope(allow=[...])` (SIFT v0.4) para restringir visibilidade e
    execução por chat. Assim o índice (embeddings) é construído uma única vez
    por usuário, não uma vez por combinação de ferramentas.
    """
    try:
        s = get_settings()
        # Code mode: run_code executa em subprocesso isolado da SIFT (tool calls
        # proxiadas por stdio, env limpo, rlimits de CPU/memória). O meta-tool só
        # chega ao modelo quando o chamador envia code_tools() — a exposição é
        # decidida por modelo (ModelConfig.code_mode), não aqui.
        # index_cache: warm start do índice entre restarts (validado por hash na SIFT).
        sift = Sift(
            sandbox=SubprocessSandbox(
                timeout=float(s.sift_code_timeout_seconds),
                cpu_seconds=s.tool_cpu_seconds,
                memory_mb=s.sift_code_mem_mb,
            ),
            index_cache=_index_cache_path(user_id),
        )
        _register_builtins(sift, search_cfg, None, finance_cfg, deep_cfg, user_id, google_cfg, tuya_cfg)
        for t in tool_rows:
            if not t.enabled:
                continue
            # integrações MCP ainda não executam (só config salva); não registra
            # na SIFT p/ o modelo não enxergar uma tool que não responde.
            if getattr(t, "tool_type", "code") == "mcp":
                continue
            try:
                _register_user_tool(
                    sift,
                    path=t.path,
                    description=t.description or "",
                    params=t.params or {},
                    returns=t.returns or [],
                    code=t.code or "",
                    valves=t.valves or {},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Tool '%s' ignorada (erro de registro): %s", t.path, exc)
        sift.build_index()
        return sift
    except Exception as exc:  # noqa: BLE001
        logger.warning("SIFT indisponível (chat seguirá sem ferramentas): %s", exc)
        return None


def get_user_sift(
    user_id: str, tool_rows: list[Any], search_cfg: SearchConfig,
    finance_cfg: "finance.FinanceConfig | None" = None,
    deep_cfg: "deep_search.DeepSearchConfig | None" = None,
    google_cfg: "GoogleConfig | None" = None,
    tuya_cfg: "TuyaConfig | None" = None,
) -> Sift | None:
    sig = _signature(tool_rows, search_cfg, finance_cfg, deep_cfg, google_cfg, tuya_cfg)
    cached = _cache.get(user_id)
    if cached is not None and cached[0] == sig:
        return cached[1]
    sift = build_user_sift(tool_rows, search_cfg, user_id, finance_cfg, deep_cfg, google_cfg, tuya_cfg)
    _cache[user_id] = (sig, sift)
    return sift


def invalidate(user_id: str) -> None:
    _cache.pop(user_id, None)


def cache_info() -> dict[str, Any]:
    """Estado do cache de instâncias SIFT (para o painel de debug)."""
    out = []
    for uid, (_sig, sift) in _cache.items():
        tools = 0
        if sift is not None:
            try:
                tools = len(sift.openai_tools())
            except Exception:  # noqa: BLE001
                tools = -1
        out.append({"user_id": uid, "ready": sift is not None, "meta_tools": tools})
    return {"cached_users": len(_cache), "instances": out}


def search_config_from_secrets(
    tavily: str | None, brave: str | None, web_prefs: dict | None = None
) -> SearchConfig:
    """Config de busca: prefs do usuário (profile.web_search) sobrepõem o env."""
    s = get_settings()
    prefs = web_prefs or {}
    provider = (prefs.get("primary") or s.web_search_provider or "duckduckgo")
    try:
        max_results = int(prefs.get("max_results") or s.web_search_max_results)
    except (TypeError, ValueError):
        max_results = s.web_search_max_results
    max_results = max(1, min(max_results, 15))
    searxng_url = (prefs.get("searxng_url") or s.searxng_url)
    providers = tuple(p for p in (prefs.get("providers") or ()) if isinstance(p, str) and p)
    domain_filter = tuple(
        d.strip() for d in str(prefs.get("domain_filter") or "").split(",") if d.strip()
    )
    return SearchConfig(
        provider=provider,
        max_results=max_results,
        searxng_url=searxng_url,
        tavily_api_key=tavily,
        brave_api_key=brave,
        providers=providers,
        multi=bool(prefs.get("multi")),
        domain_filter=domain_filter,
    )


def finance_config_from_secrets(
    finnhub: str | None, alphavantage: str | None, finance_prefs: dict | None = None
) -> "finance.FinanceConfig":
    """Config de finanças: ordem de provedores (profile.finance) + chaves.

    `providers` é a ordem de tentativa; provedores sem chave são pulados. Yahoo é o
    padrão (sem chave). `web_fallback` tenta a pesquisa na web se todos falharem."""
    prefs = finance_prefs or {}
    provs = tuple(
        p for p in (prefs.get("providers") or ["yahoo"])
        if isinstance(p, str) and p in finance.VALID_PROVIDERS
    ) or ("yahoo",)
    # modo do card: "always" = mostra o card por padrão; qualquer outro (default) =
    # texto por padrão, card só quando o usuário pedir.
    return finance.FinanceConfig(
        providers=provs,
        finnhub_key=finnhub,
        alphavantage_key=alphavantage,
        web_fallback=bool(prefs.get("web_fallback")),
        default_card=(prefs.get("card_mode") == "always"),
    )


def deep_config_from_secrets(
    openrouter_key: str | None, deep_prefs: dict | None = None
) -> "deep_search.DeepSearchConfig":
    """Config do Deep Search: chave OpenRouter (p/ os passos internos) + prefs."""
    p = deep_prefs or {}

    def _int(k: str, d: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(int(p.get(k, d)), hi))
        except (TypeError, ValueError):
            return d

    return deep_search.DeepSearchConfig(
        api_key=openrouter_key,
        model=str(p.get("model") or ""),
        max_subqueries=_int("max_subqueries", 3, 1, 6),
        max_rounds=_int("max_rounds", 2, 1, 4),
        max_results_per_query=_int("max_results_per_query", 4, 1, 8),
        read_pages=p.get("read_pages", True) is not False,
        max_pages=_int("max_pages", 4, 0, 10),
        max_page_chars=_int("max_page_chars", 4000, 500, 12000),
    )


def google_config_from_secrets(
    user_id: str, accounts: list[dict] | None = None, google_prefs: dict | None = None
) -> "GoogleConfig":
    """Config das tools Google. `accounts` = contas liberadas p/ este modelo
    ([{"id","email"}]); vazio → as tools existem mas respondem 'não conectado'.
    Preferências (ops de ativação, confirmação, limites) vêm do config por-modelo."""
    p = google_prefs or {}

    def _int(k: str, d: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(int(p.get(k, d)), hi))
        except (TypeError, ValueError):
            return d

    return GoogleConfig(
        user_id=user_id,
        require_confirm=p.get("require_confirm", True) is not False,
        max_results=_int("max_results", 10, 1, 50),
        default_calendar=str(p.get("default_calendar") or "primary"),
        accounts=list(accounts or []),
        ops=p.get("ops") if isinstance(p.get("ops"), dict) else {},
    )


def tuya_config_from_secrets(
    conn: dict | None, tuya_prefs: dict | None = None
) -> "TuyaConfig":
    """Config da tool Tuya. `conn` = conexão global (creds + catálogo) do app_settings;
    None → a tool existe mas responde 'não conectado'. As prefs (dispositivos e ações
    liberadas, confirmação) vêm do config por-modelo."""
    p = tuya_prefs or {}
    return TuyaConfig(
        conn=conn or {},
        allowed_devices=[str(d) for d in (p.get("devices") or [])],
        require_confirm=p.get("require_confirm", True) is not False,
        ops=p.get("ops") if isinstance(p.get("ops"), dict) else {},
    )


def run_user_code(code: str, params: dict[str, Any], valves: dict[str, Any] | None = None) -> Any:
    """Executa o código de uma tool no sandbox (para o botão 'testar')."""
    return run_in_subprocess(code, params, valves)


def valves_defaults(code: str) -> dict[str, Any]:
    """Defaults das valves (dict VALVES) declaradas no código."""
    return extract_valves(code)
