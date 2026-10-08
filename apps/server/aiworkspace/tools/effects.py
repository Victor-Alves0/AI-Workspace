"""Livro de efeitos do turno: uma nova TENTATIVA não repete ação já feita.

O guarda de saída (``run_turn_guarded``) rejeita a resposta e roda o turno INTEIRO de
novo. Isso é certo para texto, mas não para ações: "mande um e-mail para o João" →
1ª tentativa envia → o guarda rejeita o texto → a 2ª tentativa enviaria DE NOVO.

Aqui cada execução de ferramenta de uma tentativa fica anotada (ferramenta, argumentos,
resultado). Nas tentativas seguintes do MESMO turno, antes de executar:

- leitura ou ação repetível sem dano (buscar, ler, gravar arquivo, ligar a luz) →
  executa normalmente: refazer dá o mesmo estado, e reescrever o arquivo melhorado é
  justamente o objetivo de uma nova tentativa;
- ação externa não repetível (enviar, criar, postar, push…) → NÃO executa: devolve o
  resultado da vez anterior, mesmo que os argumentos tenham mudado (a 2ª redação do
  mesmo e-mail continua sendo o mesmo e-mail);
- o resto (rodar comando, clicar no navegador, ferramenta desconhecida) → só
  reaproveita quando a chamada é IDÊNTICA; com argumentos diferentes, executa.

O livro vive num contextvar: as tools rodam em threads com o contexto copiado do turno
(``_dispatch_tp``) e o ``call()`` do run_code passa pelo mesmo ``Sift.execute_tool``.
Trabalho em segundo plano disparado pelo turno (``bg.spawn``) sai sem livro — ele vive
além do turno e não é uma "tentativa".
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import threading
from dataclasses import dataclass, field
from typing import Any

# READ = leitura OU repetível sem dano (roda de novo); EFFECT = não repetível;
# OTHER = incerto (só a chamada idêntica é reaproveitada)
READ, EFFECT, OTHER = "read", "effect", "other"

# Por ferramenta: classe fixa (str) ou por ação ({ação: classe}, "*" = demais ações).
# Ferramenta fora da tabela → OTHER (só reaproveita chamada idêntica): o erro seguro.
_TABLE: dict[str, str | dict[str, str]] = {
    "utils.time.now": READ, "utils.math.eval": READ, "user.profile.get": READ,
    "web.search.query": READ, "web.page.read": READ, "github.public.search": READ,
    "security.exploitdb.search": READ, "security.cve.search": READ,
    "code.graph.query": READ, "code.files.browse": READ, "code.exec.jobs": READ,
    "code.flow.analyze": READ, "finance.quote.get": READ, "spotify.music.search": READ,
    "chart.render.plot": READ, "diagram.excalidraw.render": READ, "visual.widget.show": READ,
    "vercel.projects.manage": READ,
    "task.ledger.track": {"get": READ, "*": EFFECT},
    "skills.library.manage": {"list": READ, "read": READ, "*": EFFECT},
    "prompts.library.manage": {"list": READ, "read": READ, "*": EFFECT},
    "web.browser.use": {"read": READ, "screenshot": READ, "*": OTHER},
    "code.files.write": {"push": EFFECT, "*": READ},
    # comando: o mesmo comando não roda 2x (um `git push`, um deploy); outro comando roda
    "code.exec.run": OTHER,
    "code.preview.serve": {"status": READ, "wait": READ, "logs": READ, "list": READ,
                           "wake_ready": READ, "*": OTHER},
    "http.session.use": {"history": READ, "*": OTHER},
    "code.task.manage": {"list": READ, "diff": READ, "discard": READ, "*": EFFECT},
    "automation.monitor.create": EFFECT, "automation.reminder.create": EFFECT,
    "google.gmail.mailbox": {"send": EFFECT, "*": READ},
    "google.calendar.events": {"create": EFFECT, "*": READ},
    "smartlife.tuya.devices": {"scene": EFFECT, "*": READ},
    "elevenlabs.audio.generate": {"voices": READ, "*": OTHER},
    "civitai.media.use": {"generate": EFFECT, "*": READ},
    "higgsfield.media.generate": {"image": EFFECT, "video": EFFECT, "*": READ},
    "github.repo.manage": {"create_issue": EFFECT, "comment": EFFECT, "create_pr": EFFECT,
                           "*": READ},
    "notion.workspace.manage": {"create_page": EFFECT, "append": EFFECT, "*": READ},
    "slack.workspace.manage": {"send": EFFECT, "*": READ},
    "messaging.chat.manage": {"send_message": EFFECT, "*": READ},
    "remote.terminal.run": {"hosts": READ, "job": READ, "egress": READ, "*": OTHER},
    # fora da SIFT (despachadas pelo orquestrador)
    "delegate": EFFECT, "delegate_team": EFFECT,
}
# default da ação quando o modelo omite `action` numa tool que a tem
_DEFAULT_ACTION = {"remote.terminal.run": "run"}


def _action(path: str, params: dict | None) -> str:
    p = params or {}
    a = p.get("action") if isinstance(p, dict) else None
    return str(a or _DEFAULT_ACTION.get(path, "")).strip().lower()


def classify(path: str, params: dict | None = None) -> str:
    spec = _TABLE.get(path)
    if spec is None:
        return OTHER
    if isinstance(spec, str):
        return spec
    return spec.get(_action(path, params), spec.get("*", OTHER))


def _digest(params: Any) -> str:
    try:
        raw = json.dumps(params or {}, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        raw = repr(params)
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:24]


@dataclass
class _Entry:
    path: str
    action: str
    digest: str
    kind: str
    params: Any
    result: Any
    attempt: int
    reused: bool = False


@dataclass
class Ledger:
    attempt: int = 1
    entries: list[_Entry] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def next_attempt(self) -> None:
        with self._lock:
            self.attempt += 1
            for e in self.entries:  # cada tentativa pode reaproveitar tudo de novo
                e.reused = False

    def reuse(self, path: str, params: Any) -> tuple[bool, Any]:
        """(True, resultado anterior) quando esta chamada NÃO deve rodar de novo."""
        kind = classify(path, params if isinstance(params, dict) else None)
        if kind == READ or self.attempt <= 1:
            return False, None
        act = _action(path, params if isinstance(params, dict) else None)
        dig = _digest(params)
        with self._lock:
            prev = [e for e in self.entries if e.attempt < self.attempt and not e.reused
                    and e.path == path and e.action == act]
            same = next((e for e in prev if e.digest == dig), None)
            if same is not None:
                same.reused = True
                return True, _mark(same.result, identical=True)
            if kind == EFFECT and prev:
                e = prev[0]
                e.reused = True
                return True, _mark(e.result, identical=False, params=e.params)
        return False, None

    def record(self, path: str, params: Any, result: Any) -> None:
        kind = classify(path, params if isinstance(params, dict) else None)
        if kind == READ or _failed(result):
            return
        with self._lock:
            self.entries.append(_Entry(path, _action(path, params if isinstance(params, dict) else None),
                                       _digest(params), kind, params, result, self.attempt))

    def prompt_block(self) -> str:
        """O que já aconteceu nas tentativas anteriores — vai no system da próxima."""
        feitos = [e for e in self.entries if e.attempt < self.attempt and e.kind == EFFECT]
        if not feitos:
            return ""
        linhas = [f"- {e.path}{' ' + e.action if e.action else ''}: {_brief(e.params)}" for e in feitos[:20]]
        return ("ACTIONS ALREADY DONE in a previous attempt of THIS reply (they really "
                "happened and were not undone):\n" + "\n".join(linhas) + "\n"
                "Do NOT do them again. Calling them again returns the earlier result "
                "without repeating the action. Just write the reply.")


def _failed(result: Any) -> bool:
    if isinstance(result, dict):
        return bool(result.get("error")) and len(result) <= 3
    return False


def _brief(params: Any) -> str:
    try:
        s = json.dumps(params, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = str(params)
    return s[:200] + ("…" if len(s) > 200 else "")


def _mark(result: Any, *, identical: bool, params: Any = None) -> Any:
    if identical:
        note = ("already done in an earlier attempt of this reply — NOT executed again; "
                "this is that run's result")
    else:
        note = ("an equivalent action already ran in an earlier attempt of this reply "
                f"(with {_brief(params)}) — NOT executed again, to avoid doing it twice; "
                "this is that run's result. If the user truly wants another one, ask them")
    if isinstance(result, dict):
        return {**result, "replayed": note}
    if isinstance(result, str):
        try:
            obj = json.loads(result)
        except (json.JSONDecodeError, ValueError):
            return result
        if isinstance(obj, dict):
            return json.dumps({**obj, "replayed": note}, ensure_ascii=False, default=str)
    return result


current: contextvars.ContextVar[Ledger | None] = contextvars.ContextVar("effects_ledger", default=None)


def wrap_sift(sift: Any) -> Any:
    """Faz o ``execute_tool`` DESTA instância consultar o livro do turno. É o funil
    único: meta-tool execute_tool, tool fixada (nome com __) e o call() do run_code."""
    orig = sift.execute_tool
    if getattr(orig, "_effects", False):
        return sift

    def execute_tool(path: str, params: dict | None = None):
        led = current.get()
        if led is None:
            return orig(path, params)
        hit, prev = led.reuse(path, params)
        if hit:
            return prev
        res = orig(path, params)
        led.record(path, params, res)
        return res

    execute_tool._effects = True  # type: ignore[attr-defined]
    sift.execute_tool = execute_tool
    return sift
