"""PriceWatcher — dispara quando o preço de um ativo cruza um limite ou varia X%.

config: {
  "symbol": "PETR4.SA",         # obrigatório (ticker Yahoo)
  "op": "above"|"below"|"pct",  # tipo de condição
  "value": 40                    # limite (above/below) ou variação % (pct)
}
Usa o mesmo `finance.fetch_quote` do card de ações (Yahoo, sem chave). Dispara na
BORDA (quando a condição passa a ser satisfeita), não a cada checagem.
"""

from __future__ import annotations

from ... import finance


async def check(config: dict, state: dict, deps) -> dict:
    symbol = (config.get("symbol") or "").strip()
    if not symbol:
        return {"changed": False, "new_state": state, "error": "informe o ticker (symbol)"}
    cfg = deps.finance_cfg or finance.FinanceConfig()

    q = await finance.fetch_quote(symbol, cfg, config.get("range") or "1d")
    if q.get("error"):
        return {"changed": False, "new_state": state, "error": q["error"]}

    try:
        price = float(q.get("price"))
    except (TypeError, ValueError):
        return {"changed": False, "new_state": state, "error": "cotação sem preço"}
    change_pct = q.get("change_pct")
    op = (config.get("op") or "above").lower()
    try:
        value = float(config.get("value"))
    except (TypeError, ValueError):
        value = 0.0

    if op == "below":
        met = price <= value
        cond_txt = f"abaixo de {value}"
    elif op == "pct":
        met = change_pct is not None and abs(float(change_pct)) >= value
        cond_txt = f"variação de ±{value}%"
    else:  # above
        met = price >= value
        cond_txt = f"acima de {value}"

    was_met = bool(state.get("was_met"))
    new_state = {"was_met": met, "last_price": price}
    changed = met and not was_met  # borda: só dispara ao ENTRAR na condição

    cur = q.get("currency") or ""
    name = q.get("name") or symbol
    summary = (
        f"{name} ({symbol}) está em {price} {cur}"
        + (f" ({change_pct:+.2f}% no dia)" if isinstance(change_pct, (int, float)) else "")
        + f" — condição atingida: preço {cond_txt}."
    )
    return {"changed": changed, "summary_input": summary if changed else "", "new_state": new_state}
