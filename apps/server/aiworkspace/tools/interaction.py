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


# Categorias de ação que exigem aprovação (as mesmas que o dono libera numa automação).
ACTION_CATEGORIES: dict[str, str] = {
    "google": "Gmail e Agenda (enviar, arquivar, criar e apagar eventos)",
    "messaging": "Mensagens (WhatsApp, Telegram, Discord)",
    "slack": "Slack (enviar mensagens)",
    "github": "GitHub (issues, comentários, PRs, arquivos)",
    "notion": "Notion (criar e editar páginas)",
    "tuya": "Casa (ligar e desligar aparelhos)",
    "codespace": "Codespace (apagar arquivos, enviar commits)",
    "remote": "Terminal remoto (rodar comandos)",
    "civitai": "Civitai (gerar imagens pagas)",
}

_CONFIRM_OPTS = [
    {"label": "Confirmar", "value": "Sim, confirmo — refaça a ação agora com confirm=true."},
    {"label": "Cancelar", "value": "Cancele, não execute a ação."},
]


def confirm_gate(category: str, summary: str, *, required: bool, confirmed: bool,
                 options: list[dict] | None = None) -> dict[str, Any] | None:
    """A regra ÚNICA de aprovação de ações. None = pode executar; dict = devolva isto.

    - chat na tela: com a confirmação ligada, cartão Confirmar/Cancelar (o usuário
      clica e o modelo refaz com confirm=true);
    - canal (WhatsApp/Telegram/…): a pessoa está na conversa — a IA pergunta ali;
    - sem ninguém (automação/API): `confirm=true` do modelo NÃO vale (não há quem
      tenha confirmado). Age só no que o dono liberou antes; senão, recusa. A falta
      de humano nunca vira autorização — um e-mail lido pela automação pode trazer
      instruções plantadas.
    """
    from . import toolctx

    mode = toolctx.approval.get()
    if mode == "unattended":
        ok = toolctx.preauthorized.get()
        if "*" in ok or category in ok:
            return None
        nome = ACTION_CATEGORIES.get(category, category)
        return {
            "error": (f"ação bloqueada: \"{summary}\" precisa da aprovação do dono, e esta "
                      f"execução é automática (sem ninguém para aprovar). Para liberar, o dono "
                      f"marca \"{nome}\" em \"Pode agir sem perguntar\" na automação (ou dá "
                      "à chave de API a permissão de agir). Não tente de novo; informe isso "
                      "no resultado."),
            "needs_approval": category,
        }
    if not required or confirmed:
        return None
    if mode == "conversation":
        return {
            "needs_confirmation": summary,
            "note": ("Ask the user, in this conversation, to confirm this exact action. Only "
                     "after they clearly say yes, call the tool again with confirm=true."),
        }
    return ask_options(summary, options or _CONFIRM_OPTS, allow_custom=False)
