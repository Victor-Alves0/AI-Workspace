"""Preview vivo do Codespace: sobe um SERVIDOR de desenvolvimento/backend do projeto
(`npm run dev`, `python app.py`, `gradlew bootRun`…) e o mantém no ar entre turnos, pra
o usuário abrir e testar a aplicação rodando (com backend), não só ver o arquivo.

Diferente do exec normal (roda→mata no timeout) e do exec_jobs (comando que TERMINA):
um servidor NUNCA termina, então:
- sobe via `exec_service.spawn_server` (sem RLIMIT_CPU — senão morreria em horas);
- uma thread leitora enche um ring buffer de logs e detecta a queda do processo;
- o status ("subindo/no ar/caiu/parado") é calculado AO VIVO por um connect TCP na porta
  (sem thread de health dedicada);
- um reaper mata previews velhos demais; o shutdown derruba todos.

Exposição controlada pela IA (não pela UI): `expose="localhost"` (bind 127.0.0.1, padrão,
seguro — só a própria máquina/o preview embutido) ou `"lan"` (bind 0.0.0.0 — alcançável na
rede local, pra testar no celular/outro device). O usuário diz "roda em localhost" / "muda
pra LAN" e a IA passa o parâmetro; a app "sabe" porque é instruída.

Registro em memória por-processo (como `generation._active`/`exec_jobs`): um restart do
servidor derruba os previews — o subprocesso também não sobreviveria.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from ..config import get_settings
from . import exec_service

logger = logging.getLogger(__name__)

_LOG_LINES = 400  # cauda de log guardada por preview


class Preview:
    def __init__(self, *, user_id: str, project_id: str, command: str,
                 port: int, expose: str, host: str):
        self.id = uuid.uuid4().hex[:12]
        self.user_id = user_id
        self.project_id = project_id
        self.command = command
        self.port = port
        self.expose = expose            # "localhost" | "lan"
        self.host = host                # bind: 127.0.0.1 | 0.0.0.0
        self.log: deque[str] = deque(maxlen=_LOG_LINES)
        self.started_at = time.monotonic()
        self.proc = None                # subprocess.Popen
        self.exit_code: int | None = None
        self._stopping = False          # parada intencional (→ "parado", não "caiu")
        self._ended = False             # o processo terminou (leitora viu EOF)
        self._lock = threading.Lock()

    # -- liveness calculada ao vivo -----------------------------------------
    def _port_open(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=0.4):
                return True
        except OSError:
            return False

    def status(self) -> str:
        if self._ended:
            return "stopped" if self._stopping else "crashed"
        return "up" if self._port_open() else "starting"

    def summary(self, *, with_logs: bool = False, tail: int = 60) -> dict[str, Any]:
        # marcador que a UI reescreve p/ a origem PRÓPRIA do preview (http://<host>:porta/).
        # A porta está no próprio caminho → o front constrói o link direto; o app roda na
        # RAIZ dessa origem, então login/redirect/SPA/websocket funcionam sem reescrita.
        base = f"/codespace/preview/{self.port}/"
        out: dict[str, Any] = {
            "id": self.id, "command": self.command, "port": self.port,
            "expose": self.expose, "status": self.status(),
            "preview_url": base,
            "age_seconds": round(time.monotonic() - self.started_at, 1),
            "exit_code": self.exit_code,
        }
        if with_logs:
            out["logs"] = "\n".join(list(self.log)[-tail:]) or "(sem saída ainda)"
        return out


# registro por-processo: id -> Preview
_previews: dict[str, Preview] = {}
_reg_lock = threading.Lock()


def _reader(pv: Preview) -> None:
    """Consome stdout/stderr do servidor até EOF, enchendo o ring de logs. EOF = o
    processo terminou (queda ou parada intencional)."""
    try:
        assert pv.proc is not None and pv.proc.stdout is not None
        for line in pv.proc.stdout:
            pv.log.append(line.rstrip("\n"))
    except Exception:  # noqa: BLE001
        pass
    finally:
        with pv._lock:
            pv._ended = True
            try:
                pv.exit_code = pv.proc.poll() if pv.proc else None
            except Exception:  # noqa: BLE001
                pv.exit_code = None


def _env_for(pv: Preview) -> dict[str, str]:
    # dicas de bind/porta que os frameworks comuns respeitam, pra o app escutar em
    # 0.0.0.0:<porta publicada> sem a IA ter que lembrar o flag de cada um. Se o framework
    # ignorar o env, a IA ainda deve passar o flag no comando (ex.: `vite --host 0.0.0.0
    # --port <porta>`, `next start -p <porta>`).
    p = str(pv.port)
    env = {
        "HOST": pv.host, "HOSTNAME": pv.host, "PORT": p,
        "FLASK_RUN_HOST": pv.host, "FLASK_RUN_PORT": p,
        "MB_JETTY_HOST": pv.host, "MB_JETTY_PORT": p,   # Metabase
        "SERVER_PORT": p, "HTTP_PORT": p,               # Spring Boot / genéricos
        "BROWSER": "none",  # não tenta abrir um navegador no servidor
    }
    # dir de dados persistente do projeto (sobrevive a restart) — o app aponta estado
    # durável (DB embarcado, uploads) aqui, e assim o preview mantém estado entre turnos.
    try:
        from . import graph_service
        env["WORKSPACE_DATA"] = str(graph_service.project_data_dir(pv.user_id, pv.project_id))
    except Exception:  # noqa: BLE001
        pass
    return env


# porta ESTÁVEL por projeto: um projeto sempre reabre o preview na MESMA porta (link não
# muda a cada start). Lembrada em memória (previews não sobrevivem a restart do server de
# qualquer forma). Ver [[codespace-live-preview]].
_project_port: dict[str, int] = {}


def _pick_port(project_id: str, requested: int) -> int | None:
    """Porta do preview no range publicado (own-origin), ESTÁVEL por projeto: se o projeto
    já tem um preview vivo → a MESMA porta (reiniciar reusa); senão a porta que ele já usou
    antes (se livre); senão a pedida (se no range e livre); senão a menor livre. Assim o
    LINK não muda toda vez. None = range lotado."""
    s = get_settings()
    lo, hi = int(s.code_preview_port_min), int(s.code_preview_port_max)
    # 1) preview vivo do próprio projeto → estabilidade (o start substitui esse processo)
    for pv in _previews.values():
        if pv.project_id == project_id and not pv._ended:
            return pv.port
    taken = {pv.port for pv in _previews.values() if not pv._ended}
    # 2) porta histórica do projeto, se ainda no range e livre
    prev = _project_port.get(project_id)
    if prev and lo <= prev <= hi and prev not in taken:
        return prev
    # 3) pedida (se no range e livre); 4) senão a menor livre
    if lo <= requested <= hi and requested not in taken:
        return requested
    for cand in range(lo, hi + 1):
        if cand not in taken:
            return cand
    return None


def start_preview(user_id: str, project_id: str, root: Path, command: str,
                  port: int, expose: str = "localhost") -> dict[str, Any]:
    """Sobe um servidor de preview (SÍNCRONO — chamado da tool no threadpool)."""
    s = get_settings()
    if not (command or "").strip():
        return {"error": "informe o comando que sobe o servidor (ex.: 'npm run dev')"}
    try:
        port = int(port)
    except (TypeError, ValueError):
        return {"error": "porta inválida"}

    with _reg_lock:
        # own-origin: a porta tem que estar no range PUBLICADO no host (senão o navegador
        # não alcança o app). Escolhe a pedida (se no range e livre) ou a menor livre.
        chosen = _pick_port(project_id, port)
        if chosen is None:
            return {"error": f"todas as portas de preview ({s.code_preview_port_min}-"
                             f"{s.code_preview_port_max}) estão em uso — pare algum preview antes"}
        port = chosen
        _project_port[project_id] = port  # fixa a porta do projeto (link estável)
        # sempre 0.0.0.0: a porta é publicada no host, então o app precisa escutar em todas
        # as interfaces do container pra o Docker encaminhar. `expose` fica só informativo.
        host = "0.0.0.0"
        expose = "lan" if str(expose).lower() in ("lan", "0.0.0.0", "network") else "localhost"
        # UM preview por projeto: substitui QUALQUER preview anterior do mesmo projeto
        # (reinício) — evita acumular preview velho e mantém a mesma porta/link.
        for pv in list(_previews.values()):
            if pv.project_id == project_id:
                _stop(pv)
                _previews.pop(pv.id, None)
        # teto por usuário
        mine = [pv for pv in _previews.values() if pv.user_id == user_id]
        if len(mine) >= int(s.code_preview_max_per_user):
            return {"error": f"limite de {s.code_preview_max_per_user} previews no ar — "
                             "pare algum antes de subir outro"}
        pv = Preview(user_id=user_id, project_id=project_id, command=command,
                     port=port, expose=expose, host=host)
        try:
            pv.proc = exec_service.spawn_server(command, root, env_extra=_env_for(pv))
        except (OSError, ValueError) as exc:
            return {"error": f"não consegui iniciar o servidor: {exc}"}
        _previews[pv.id] = pv

    threading.Thread(target=_reader, args=(pv,), daemon=True).start()
    # dá um instante pra falha imediata (comando inexistente) aparecer no status
    time.sleep(0.4)
    out = pv.summary(with_logs=True, tail=20)
    out["note"] = (
        f"O servidor está subindo. Use a ação `wait` (com este preview_id) — ela BLOQUEIA "
        f"até ficar 'up' ou cair, num único call, em vez de você ficar chamando `status` em "
        f"loop. SEJA PACIENTE: apps de backend pesados (JVM/Metabase/Spring, Rails, Django) "
        f"levam de 1 a 3 MINUTOS no 1º boot p/ baixar deps e RODAR MIGRAÇÕES — 'starting'/503 "
        f"'initializing' é NORMAL; só desista se virar 'crashed'. Se quiser que o estado do app "
        f"(DB embarcado, uploads) SOBREVIVA a reinícios, aponte-o para $WORKSPACE_DATA (dir "
        f"persistente do projeto, fora do git). DÊ AO USUÁRIO UM LINK CLICÁVEL em markdown que abre "
        f"o app numa nova guia: [abrir o app]({pv.summary()['preview_url']}) — a UI abre o "
        f"app na PRÓPRIA porta ({pv.port}), então login/redirect/SPA/websocket funcionam "
        f"nativamente (roda na raiz, sem prefixo). O app precisa escutar em 0.0.0.0:{pv.port} "
        f"— o env já traz HOST/PORT/MB_JETTY_PORT/SERVER_PORT={pv.port}; se o framework "
        f"ignorar o env, passe o flag no comando (ex.: `--host 0.0.0.0 --port {pv.port}`). "
        f"A porta é FIXA por projeto ({pv.port}) — reiniciar reusa a MESMA porta e substitui "
        f"o processo antigo, então o LINK NÃO MUDA; não invente uma porta nova a cada start. "
        f"HMR funciona nativo — não passe base path."
    )
    return out


def _stop(pv: Preview) -> None:
    pv._stopping = True
    if pv.proc is not None:
        exec_service.kill_tree(pv.proc)


def stop_preview(user_id: str, preview_id: str) -> dict[str, Any]:
    with _reg_lock:
        pv = _previews.get(preview_id)
        if pv is None or pv.user_id != user_id:
            return {"error": "preview não encontrado"}
        _stop(pv)
        _previews.pop(pv.id, None)
    return {"ok": True, "stopped": preview_id}


def preview_status(user_id: str, preview_id: str, *, with_logs: bool = True,
                   tail: int = 80) -> dict[str, Any]:
    pv = _previews.get(preview_id)
    if pv is None or pv.user_id != user_id:
        return {"error": "preview não encontrado"}
    return pv.summary(with_logs=with_logs, tail=tail)


def list_previews(user_id: str, project_id: str | None = None) -> dict[str, Any]:
    with _reg_lock:
        items = [
            pv.summary() for pv in _previews.values()
            if pv.user_id == user_id and (project_id is None or pv.project_id == project_id)
        ]
    return {"previews": items}


def wait_ready(user_id: str, project_id: str | None, ident: str,
               timeout: float = 120) -> dict[str, Any]:
    """Bloqueia (no threadpool da tool) até o preview ficar 'up', cair, ou o tempo
    esgotar — em vez de o agente fazer polling em rajada. Um único call resolve o que
    antes eram dezenas de `status`. Se ainda 'starting' no fim, o agente chama de novo
    (backends pesados levam minutos). timeout capado em 240s por chamada."""
    pv = _find_owned(user_id, project_id, ident)
    if pv is None:
        return {"error": "preview não encontrado (ou não é seu)"}
    deadline = time.monotonic() + min(max(float(timeout or 120), 1), 240)
    while time.monotonic() < deadline:
        st = pv.status()
        if st == "up":
            return {**pv.summary(with_logs=True, tail=8), "note": "no ar (porta respondendo)."}
        if st in ("crashed", "stopped"):
            return {**pv.summary(with_logs=True, tail=25),
                    "note": "o servidor CAIU/parou antes de subir — veja os logs (erro real)."}
        time.sleep(1.5)
    return {**pv.summary(with_logs=True, tail=12),
            "note": "ainda subindo quando o tempo de espera acabou — isso é NORMAL p/ "
                    "backend pesado (JVM/Metabase levam minutos). Chame `wait` de novo; "
                    "só desista se o status virar 'crashed'."}


def _find_owned(user_id: str, project_id: str | None, ident: str) -> "Preview | None":
    """Acha um preview do usuário (e do projeto, se dado) por id OU por porta."""
    ident = str(ident).strip()
    with _reg_lock:
        for pv in _previews.values():
            if pv.user_id != user_id:
                continue
            if project_id is not None and pv.project_id != project_id:
                continue
            if pv.id == ident or str(pv.port) == ident:
                return pv
    return None


def port_owned_by(user_id: str, port: int) -> bool:
    """O usuário tem um preview vivo nesta porta? (autoriza o reverse-proxy)."""
    try:
        port = int(port)
    except (TypeError, ValueError):
        return False
    with _reg_lock:
        return any(pv.user_id == user_id and pv.port == port for pv in _previews.values())


_PROBE_BODY_CAP = 200_000


def request_preview(user_id: str, project_id: str | None, preview_id: str,
                    method: str = "GET", path: str = "/",
                    headers: dict | None = None, body: str | None = None) -> dict[str, Any]:
    """Manda uma requisição HTTP ao PRÓPRIO preview do projeto (roda no server, mesmo
    host do preview → alcança 127.0.0.1:porta em desktop e Docker). Anônimo por padrão
    (não injeta credencial) — é o "teste/ataque ao alvo vivo" agêntico. NÃO segue
    redirect (o 3xx interessa) e devolve status/cabeçalhos/corpo crus."""
    import httpx

    pv = _find_owned(user_id, project_id, preview_id)
    if pv is None:
        return {"error": "preview não encontrado — suba um com 'start' ou veja o id em 'list'"}
    m = (method or "GET").upper()
    p = "/" + (path or "/").lstrip("/")
    url = f"http://127.0.0.1:{pv.port}{p}"
    content = body.encode("utf-8", "replace") if isinstance(body, str) else body
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as c:
            r = c.request(m, url, headers=headers or None, content=content)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"falha ao chamar o preview: {exc}"[:300]}
    text = r.text
    raw = text.encode("utf-8", "ignore")
    truncated = len(raw) > _PROBE_BODY_CAP
    if truncated:
        text = raw[:_PROBE_BODY_CAP].decode("utf-8", "ignore")
    return {
        "http_status": r.status_code,
        "resp_headers": dict(r.headers),
        "body": text,
        "truncated": truncated,
        "url": url,
    }


# --------------------------------------------------------------------------- #
# Reaper + shutdown (subidos no lifespan, como o exec_jobs)
# --------------------------------------------------------------------------- #
async def _reaper() -> None:
    import asyncio

    while True:
        try:
            await asyncio.sleep(120)
            max_age = int(get_settings().code_preview_max_age_seconds)
            now = time.monotonic()
            with _reg_lock:
                for pv in list(_previews.values()):
                    # mortos (caíram) ou velhos demais saem do registro
                    too_old = (now - pv.started_at) > max_age
                    if pv._ended or too_old:
                        if not pv._ended:
                            _stop(pv)
                        if pv._ended or too_old:
                            _previews.pop(pv.id, None)
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception:  # noqa: BLE001
            logger.warning("preview reaper: erro no ciclo", exc_info=True)


_reaper_task = None


def start_reaper() -> None:
    global _reaper_task
    import asyncio

    if _reaper_task is None or _reaper_task.done():
        _reaper_task = asyncio.create_task(_reaper())


async def shutdown() -> None:
    with _reg_lock:
        for pv in list(_previews.values()):
            _stop(pv)
        _previews.clear()
    global _reaper_task
    if _reaper_task is not None:
        _reaper_task.cancel()
        _reaper_task = None
