"""Cotações de ações/índices para o card de finanças.

Normaliza diferentes provedores numa MESMA estrutura compacta, consumida pela
tool `finance.quote.get` (o modelo chama) e pela rota GET /finance/quote (abas de
período do card no frontend). Tenta os provedores na ordem configurada até um
responder; opcionalmente cai para a pesquisa na web.

O modelo NÃO recebe HTML/imagem — só os números (preço, variação, série curta,
stats). O desenho do card é feito no frontend.
"""

from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

# range do card -> (range Yahoo, intervalo Yahoo)
_RANGES: dict[str, tuple[str, str]] = {
    "1d": ("1d", "5m"),
    "5d": ("5d", "30m"),
    "1mo": ("1mo", "1d"),
    "6mo": ("6mo", "1d"),
    "ytd": ("ytd", "1d"),
    "1y": ("1y", "1wk"),
    "5y": ("5y", "1wk"),
    "max": ("max", "1mo"),
}

VALID_PROVIDERS = ("yahoo", "finnhub", "alphavantage", "web")


@dataclass
class FinanceConfig:
    """Config de finanças por usuário (ordem de provedores + chaves + fallback web)."""

    providers: tuple[str, ...] = ("yahoo",)
    finnhub_key: str | None = None
    alphavantage_key: str | None = None
    web_fallback: bool = False
    # quando o modelo não especifica: mostrar o card visual (com gráfico) por padrão?
    # False (padrão) = só os números em texto; o card aparece se o usuário pedir.
    default_card: bool = False
    # busca web p/ o fallback (injetada); recebe a query e devolve list[dict]
    web_search: Callable[[str], Awaitable[list[dict]]] | None = None


# --------------------------------- helpers --------------------------------- #
def _r(x: Any, n: int = 4) -> float | None:
    try:
        return round(float(x), n)
    except (TypeError, ValueError):
        return None


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def _downsample(series: list[dict], target: int = 60) -> list[dict]:
    if len(series) <= target:
        return series
    step = len(series) / target
    out = [series[int(i * step)] for i in range(target)]
    if out[-1] is not series[-1]:
        out[-1] = series[-1]
    return out


def _finalize(out: dict) -> dict:
    p, pc = out.get("price"), out.get("prev_close")
    if isinstance(p, (int, float)) and isinstance(pc, (int, float)) and pc:
        out["change"] = _r(p - pc)
        out["change_pct"] = round((p - pc) / pc * 100, 2)
    return _clean(out)


# --------------------------------- Yahoo ----------------------------------- #
async def _yahoo(client: httpx.AsyncClient, symbol: str, range_: str) -> dict:
    yr, yi = _RANGES.get(range_, _RANGES["1d"])
    r = await client.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"range": yr, "interval": yi, "includePrePost": "false"},
        headers={"User-Agent": _UA},
        timeout=10,
    )
    r.raise_for_status()
    res = ((r.json().get("chart") or {}).get("result") or [None])[0]
    if not res:
        raise ValueError("no data")
    meta = res.get("meta") or {}
    ts = res.get("timestamp") or []
    closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    series = [{"t": int(t), "v": _r(c, 4)} for t, c in zip(ts, closes) if c is not None]

    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if price is None and series:
        price = series[-1]["v"]
    if prev is None and series:
        prev = series[0]["v"]

    out = {
        "symbol": meta.get("symbol") or symbol.upper(),
        "name": meta.get("longName") or meta.get("shortName") or meta.get("symbol") or symbol.upper(),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "currency": meta.get("currency"),
        "price": _r(price),
        "prev_close": _r(prev),
        "series": _downsample(series),
        "stats": _clean({
            "open": meta.get("regularMarketOpen") or (series[0]["v"] if series and range_ == "1d" else None),
            "high": meta.get("regularMarketDayHigh"),
            "low": meta.get("regularMarketDayLow"),
            "high_52w": meta.get("fiftyTwoWeekHigh"),
            "low_52w": meta.get("fiftyTwoWeekLow"),
            "volume": meta.get("regularMarketVolume"),
        }),
        "source": "yahoo",
        "range": range_,
    }

    # enriquecimento best-effort (nome, P/L, cap. de mercado, dividendo) — v7
    # pode exigir cookie/crumb e falhar; nesse caso seguimos com o que temos.
    try:
        r2 = await client.get(
            "https://query1.finance.yahoo.com/v7/finance/quote",
            params={"symbols": symbol},
            headers={"User-Agent": _UA},
            timeout=8,
        )
        if r2.status_code == 200:
            qr = ((r2.json().get("quoteResponse") or {}).get("result") or [])
            if qr:
                d = qr[0]
                out["name"] = d.get("longName") or d.get("shortName") or out["name"]
                out["stats"].update(_clean({
                    "open": d.get("regularMarketOpen"),
                    "high": d.get("regularMarketDayHigh"),
                    "low": d.get("regularMarketDayLow"),
                    "high_52w": d.get("fiftyTwoWeekHigh"),
                    "low_52w": d.get("fiftyTwoWeekLow"),
                    "market_cap": d.get("marketCap"),
                    "pe": d.get("trailingPE"),
                    "dividend_yield": d.get("trailingAnnualDividendYield"),
                }))
    except Exception:  # noqa: BLE001
        pass

    return _finalize(out)


# -------------------------------- Finnhub ---------------------------------- #
async def _finnhub(client: httpx.AsyncClient, symbol: str, key: str, range_: str) -> dict:
    q = await client.get(
        "https://finnhub.io/api/v1/quote",
        params={"symbol": symbol, "token": key},
        timeout=10,
    )
    q.raise_for_status()
    d = q.json()
    if not d.get("c"):
        raise ValueError("no quote")
    out = {
        "symbol": symbol.upper(),
        "name": symbol.upper(),
        "price": _r(d.get("c")),
        "prev_close": _r(d.get("pc")),
        "series": [],  # candles são premium no free tier
        "stats": _clean({"open": d.get("o"), "high": d.get("h"), "low": d.get("l")}),
        "source": "finnhub",
        "range": range_,
    }
    try:
        p = await client.get(
            "https://finnhub.io/api/v1/stock/profile2",
            params={"symbol": symbol, "token": key},
            timeout=8,
        )
        if p.status_code == 200:
            pd = p.json()
            out["name"] = pd.get("name") or out["name"]
            out["currency"] = pd.get("currency")
            out["exchange"] = pd.get("exchange")
            if pd.get("marketCapitalization"):
                out["stats"]["market_cap"] = pd["marketCapitalization"] * 1e6
    except Exception:  # noqa: BLE001
        pass
    return _finalize(out)


# ------------------------------ Alpha Vantage ------------------------------ #
async def _alphavantage(client: httpx.AsyncClient, symbol: str, key: str, range_: str) -> dict:
    q = await client.get(
        "https://www.alphavantage.co/query",
        params={"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": key},
        timeout=12,
    )
    q.raise_for_status()
    g = q.json().get("Global Quote") or {}
    if not g.get("05. price"):
        raise ValueError("no quote (rate limit?)")
    out = {
        "symbol": symbol.upper(),
        "name": symbol.upper(),
        "price": _r(g.get("05. price")),
        "prev_close": _r(g.get("08. previous close")),
        "series": [],
        "stats": _clean({"open": _r(g.get("02. open")), "high": _r(g.get("03. high")), "low": _r(g.get("04. low"))}),
        "source": "alphavantage",
        "range": range_,
    }
    if range_ == "1d":
        try:
            s = await client.get(
                "https://www.alphavantage.co/query",
                params={"function": "TIME_SERIES_INTRADAY", "symbol": symbol, "interval": "5min", "apikey": key, "outputsize": "compact"},
                timeout=15,
            )
            tsd = s.json().get("Time Series (5min)") or {}
            series = []
            for k in sorted(tsd):
                t = int(_dt.datetime.fromisoformat(k).timestamp())
                series.append({"t": t, "v": _r(tsd[k]["4. close"])})
            out["series"] = _downsample(series[-80:])
        except Exception:  # noqa: BLE001
            pass
    return _finalize(out)


# --------------------------------- web ------------------------------------- #
async def _web(symbol: str, cfg: FinanceConfig, range_: str) -> dict:
    if not cfg.web_search:
        raise ValueError("web search unavailable")
    results = await cfg.web_search(f"{symbol} stock price quote")
    trimmed = [
        {"title": (r.get("title") or "")[:100], "url": r.get("url") or "", "snippet": (r.get("content") or "").strip()[:200]}
        for r in (results or [])[:3]
    ]
    if not trimmed:
        raise ValueError("no web results")
    return {
        "symbol": symbol.upper(),
        "name": symbol.upper(),
        "source": "web",
        "range": range_,
        "web_results": trimmed,
    }


# ------------------------------- orquestração ------------------------------ #
async def fetch_quote(symbol: str, cfg: FinanceConfig, range_: str = "1d") -> dict:
    """Busca a cotação tentando os provedores em ordem; devolve dict normalizado
    ou {"error": ...}. Nunca levanta exceção p/ fora."""
    symbol = (symbol or "").strip()
    if not symbol:
        return {"error": "provide a ticker symbol (e.g. AAPL, NESN.SW, PETR4.SA)"}
    if range_ not in _RANGES:
        range_ = "1d"
    providers = tuple(p for p in (cfg.providers or ("yahoo",)) if p in VALID_PROVIDERS) or ("yahoo",)
    errors: list[str] = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for p in providers:
            try:
                if p == "yahoo":
                    return await _yahoo(client, symbol, range_)
                if p == "finnhub" and cfg.finnhub_key:
                    return await _finnhub(client, symbol, cfg.finnhub_key, range_)
                if p == "alphavantage" and cfg.alphavantage_key:
                    return await _alphavantage(client, symbol, cfg.alphavantage_key, range_)
                if p == "web":
                    return await _web(symbol, cfg, range_)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{p}: {exc}")
        if cfg.web_fallback and "web" not in providers:
            try:
                return await _web(symbol, cfg, range_)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"web: {exc}")
    return {"error": "quote unavailable — " + "; ".join(errors[:3] or ["no provider"])}
