"""Primitiva de sistema "perguntar ao usuário" (kind:"ask").

Qualquer ferramenta (builtin, script do usuário, ou uma etapa futura) pode
retornar o dict de `ask_options(...)` para mostrar ao usuário um seletor de
opções inline no chat. O frontend renderiza `kind:"ask"` como botões de acesso
rápido; a escolha do usuário volta como a próxima mensagem dele (Padrão A: sem
mágica de "await" — o turno termina e a resposta reinicia o modelo).

Contrato (o front só precisa disto):
    {"kind": "ask", "question": str, "options": [{"label","value","hint?"}],
     "allow_custom": bool, "custom_label": str}
"""

from __future__ import annotations

from typing import Any


def ask_options(
    question: str,
    options: Any,
    *,
    allow_custom: bool = True,
    custom_label: str = "Algo personalizado",
) -> dict[str, Any]:
    """Monta o artefato de seleção. `options` aceita strings ou dicts
    {label, value?, hint?}. Limita a 8 opções (acesso rápido, não um menu longo)."""
    norm: list[dict[str, str]] = []
    for o in (options or [])[:8]:
        if isinstance(o, str):
            v = o.strip()
            if v:
                norm.append({"label": v[:100], "value": v})
        elif isinstance(o, dict):
            label = str(o.get("label") or o.get("value") or "").strip()
            if not label:
                continue
            item: dict[str, str] = {"label": label[:100], "value": str(o.get("value") or label)}
            hint = o.get("hint")
            if hint:
                item["hint"] = str(hint)[:160]
            norm.append(item)
    return {
        "kind": "ask",
        "question": str(question or "").strip()[:400],
        "options": norm,
        "allow_custom": bool(allow_custom),
        "custom_label": custom_label,
    }
