"""Driver do Navegador headless (tool `web.browser.use`), em CDP puro.

Fala o Chrome DevTools Protocol direto por WebSocket (`websockets`, que o app já usa):
nada de Playwright, que pesava ~140 MB no motor só para ser um cliente CDP. Uma ABA
VIVA por conversa persiste entre chamadas de tool (login, formulários, vários passos),
cada uma num contexto de navegador isolado (cookies/armazenamento não cruzam conversas).

Dois jeitos de ter o navegador:
  - endpoint `ws://...` — um Chromium remoto (o serviço `browser`/browserless do Docker);
  - endpoint `local`    — o Edge ou o Chrome INSTALADOS na máquina, em modo headless,
    com perfil temporário. É o caminho do app desktop no Windows (o Edge sempre está lá).

Guarda de rede: além do `goto` validado pela tool, TODA requisição da página passa pela
mesma guarda anti-SSRF (domínio Fetch do CDP). Sem isso, uma página podia redirecionar
ou carregar recursos de 192.168.x/localhost — no desktop, isso é a rede da casa do
usuário (roteador, NAS).

Ponte estado × tool síncrona: as tools da SIFT são funções SÍNCRONAS (threadpool). O
driver é um SINGLETON com uma THREAD + event loop PRÓPRIOS, donos das conexões; as tools
submetem corrotinas via `run_coroutine_threadsafe`.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

LOCAL = "local"

# tetos anti-abuso
_MAX_SESSIONS = 8
_IDLE_TTL = 300.0          # fecha aba ociosa após 5 min
_NAV_TIMEOUT = 30.0        # navegação (goto/click que troca de página)
_ACTION_TIMEOUT = 15.0
_MAX_TEXT = 6000           # texto legível devolvido por ação
_MAX_ELEMENTS = 40         # elementos interativos listados
_GUARD_TTL = 300.0         # decisão da guarda de rede por host
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# Estado da página num Runtime.evaluate só: título, URL, texto e os elementos
# interativos VISÍVEIS (links/botões/campos) com o texto acessível — a IA clica/digita
# mirando esse texto (ou um seletor CSS).
_STATE_JS = """(() => {
  const out = [];
  const sel = 'a,button,input,select,textarea,[role=button],[role=link],[onclick]';
  const seen = new Set();
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    let t = (el.innerText || el.value || el.getAttribute('aria-label')
      || el.getAttribute('placeholder') || el.getAttribute('name')
      || el.getAttribute('title') || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
    if (!t) continue;
    const tag = el.tagName.toLowerCase();
    const key = tag + '|' + t;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ i: out.length + 1, tag, text: t });
    if (out.length >= %d) break;
  }
  return { title: document.title || '', url: location.href,
           text: document.body ? document.body.innerText : '', elements: out };
})()""" % _MAX_ELEMENTS

# Declarados DENTRO da função de cada chamada: `const` no escopo global da página
# quebraria na 2a chamada ("Identifier '__vis' has already been declared").
_HELPERS_JS = """
const __vis = el => { const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
  return r.width > 1 && r.height > 1 && st.visibility !== 'hidden' && st.display !== 'none'; };
const __norm = s => (s || '').toLowerCase().replace(/\\s+/g, ' ').trim();
"""

# Alvo de clique: seletor CSS, ou o texto visível — primeiro nos elementos clicáveis
# (igual exato, depois "contém"), por último o MENOR elemento que contém o texto.
_CLICK_JS = "(() => {" + _HELPERS_JS + """
return ((target, css) => {
  let el = null;
  if (css) { el = [...document.querySelectorAll(target)].find(__vis) || null; }
  else {
    const t = __norm(target);
    const txt = e => __norm(e.innerText || e.value || e.getAttribute('aria-label')
      || e.getAttribute('placeholder') || e.getAttribute('title'));
    const cands = [...document.querySelectorAll('a,button,input,select,textarea,label,summary,'
      + '[role=button],[role=link],[role=tab],[role=menuitem],[onclick]')].filter(__vis);
    el = cands.find(e => txt(e) === t) || cands.find(e => txt(e).includes(t)) || null;
    if (!el && document.body) {
      const all = [...document.body.querySelectorAll('*')]
        .filter(e => __vis(e) && __norm(e.innerText).includes(t));
      all.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
      el = all[0] || null;
    }
  }
  if (!el) return { error: 'element not found: ' + target };
  el.scrollIntoView({ block: 'center', inline: 'center' });
  const r = el.getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
})(%s, %s); })()"""

# Campo de digitação: seletor CSS; senão rótulo (<label>), placeholder, aria-label,
# name/id; sem alvo, o primeiro campo visível. Foca e SELECIONA o conteúdo — o texto
# digitado em seguida substitui o que havia (como o fill do Playwright).
_FIELD_JS = "(() => {" + _HELPERS_JS + """
return ((target, css) => {
  const campos = 'input:not([type=hidden]),textarea,[contenteditable=""],[contenteditable=true]';
  let el = null;
  if (!target) { el = [...document.querySelectorAll(campos)].find(__vis) || null; }
  else if (css) { el = [...document.querySelectorAll(target)].find(__vis) || null; }
  else {
    const t = __norm(target);
    for (const lb of document.querySelectorAll('label')) {
      if (!__norm(lb.innerText).includes(t)) continue;
      const c = lb.control || (lb.htmlFor && document.getElementById(lb.htmlFor))
        || lb.querySelector('input,textarea,select');
      if (c && __vis(c)) { el = c; break; }
    }
    if (!el) el = [...document.querySelectorAll(campos)].find(e => __vis(e)
      && [e.placeholder, e.getAttribute('aria-label'), e.name, e.id, e.title]
        .some(v => v && __norm(v).includes(t))) || null;
  }
  if (!el) return { error: 'field not found: ' + (target || '(any)') };
  el.scrollIntoView({ block: 'center' });
  el.focus();
  if (typeof el.select === 'function') el.select();
  else { const s = getSelection(); s.selectAllChildren(el); }
  return { ok: true };
})(%s, %s); })()"""


class CDPError(RuntimeError):
    pass


def _looks_css(target: str) -> bool:
    t = target.strip()
    return t.startswith((".", "#", "[")) or ">" in t or (" " not in t and any(c in t for c in ".#[]>"))


# --------------------------------------------------------------------------- #
# Navegador local (Edge/Chrome instalados)                                     #
# --------------------------------------------------------------------------- #
def find_local_browser() -> str | None:
    """Executável do Edge/Chrome/Chromium desta máquina (ou BROWSER_EXECUTABLE)."""
    forced = (os.environ.get("BROWSER_EXECUTABLE") or "").strip()
    if forced:
        return forced if Path(forced).is_file() else None
    if sys.platform == "win32":
        bases = [os.environ.get(v) for v in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
        rels = [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe",
                r"Chromium\Application\chrome.exe"]
        for rel in rels:
            for base in bases:
                if base and (Path(base) / rel).is_file():
                    return str(Path(base) / rel)
        return None
    for nome in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
                 "microsoft-edge", "microsoft-edge-stable"):
        achado = shutil.which(nome)
        if achado:
            return achado
    return None


class _LocalBrowser:
    """Um Edge/Chrome headless só nosso: perfil temporário, porta aleatória.

    No Windows o `msedge.exe` que subimos pode REPASSAR a execução a outro processo e
    sair (código 0) — o navegador de verdade fica com outro PID, e um `kill` no nosso
    não o alcança. Por isso ele roda num Job Object com KILL_ON_JOB_CLOSE (ver
    aiworkspace.winjob): fechar o job derruba a árvore inteira, e se o app cair o
    Windows fecha o job sozinho — nenhum navegador fica órfão."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.profile: str | None = None
        self.job = None

    async def start(self) -> str:
        exe = find_local_browser()
        if not exe:
            raise CDPError("nenhum Edge/Chrome/Chromium encontrado nesta máquina "
                           "(defina BROWSER_EXECUTABLE com o caminho do navegador)")
        try:
            return await self._launch(exe, sandbox=True)
        except CDPError as exc:
            # Linux: o Ubuntu 24+ bloqueia o namespace de usuário que o sandbox do
            # Chrome usa, e ele se recusa a abrir ("No usable sandbox"). O modo local no
            # Linux é só de desenvolvimento/teste (produção usa o browserless do Docker);
            # o Windows nunca passa por aqui.
            # A mensagem nem sempre diz "sandbox" (às vezes só o SIGABRT), então no
            # Linux qualquer falha ao abrir ganha uma segunda tentativa sem ele.
            if sys.platform == "win32":
                raise
            logger.warning("navegador local falhou ao abrir (%s); tentando sem sandbox", exc)
            return await self._launch(exe, sandbox=False)

    async def _launch(self, exe: str, *, sandbox: bool) -> str:
        from .. import winjob

        self.profile = tempfile.mkdtemp(prefix="aiw-browser-")
        flags = 0
        if winjob.IS_WINDOWS:
            self.job = winjob.JobObject()
            flags = 0x08000000 | winjob.CREATE_SUSPENDED  # CREATE_NO_WINDOW, suspenso até entrar no job
        args = [exe, "--headless=new", "--remote-debugging-port=0",
                f"--user-data-dir={self.profile}", "--no-first-run", "--no-default-browser-check",
                "--disable-extensions", "--disable-background-networking", "--disable-sync",
                "--mute-audio", "--hide-scrollbars"]
        if not sandbox:
            args.append("--no-sandbox")
        # a saída de erro vai para um arquivo: se o navegador cair ao abrir, o motivo
        # real entra na mensagem (antes ia para o nada e só sobrava um código de saída)
        log_path = Path(self.profile) / "browser.log"
        with open(log_path, "wb") as log:
            self.proc = subprocess.Popen(args + ["about:blank"], stdout=subprocess.DEVNULL,
                                         stderr=log, creationflags=flags)
        if self.job is not None:
            self.job.adopt(self.proc)
        # com --remote-debugging-port=0 o navegador escolhe a porta e a grava aqui
        arquivo = Path(self.profile) / "DevToolsActivePort"
        fim = time.monotonic() + 20
        while time.monotonic() < fim:
            codigo = self.proc.poll()
            if codigo not in (None, 0):  # 0 = repassou a outro processo
                motivo = self._log_tail(log_path)
                self.stop()
                raise CDPError(f"o navegador local encerrou ao iniciar (código {codigo})"
                               + (f": {motivo}" if motivo else ""))
            try:
                linhas = arquivo.read_text(encoding="utf-8").split()
                if len(linhas) >= 2:
                    return f"ws://127.0.0.1:{int(linhas[0])}{linhas[1]}"
            except (OSError, ValueError):
                pass
            await asyncio.sleep(0.1)
        motivo = self._log_tail(log_path)
        self.stop()
        raise CDPError("o navegador local não abriu a porta de depuração a tempo"
                       + (f": {motivo}" if motivo else ""))

    @staticmethod
    def _log_tail(path: Path) -> str:
        try:
            linhas = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        except OSError:
            return ""
        # o Chrome imprime o motivo numa linha FATAL/ERROR e depois o stack trace
        uteis = [ln for ln in linhas if "FATAL" in ln or "ERROR" in ln] or linhas[-3:]
        return " | ".join(uteis[-3:])[:400]

    @property
    def running(self) -> bool:
        return self.profile is not None

    def stop(self) -> None:
        if self.job is not None:
            self.job.terminate()
            self.job.close()
            self.job = None
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        self.proc = None
        if self.profile:
            # os arquivos do perfil ficam presos por um instante depois que o navegador cai
            for _ in range(20):
                shutil.rmtree(self.profile, ignore_errors=True)
                if not Path(self.profile).exists():
                    break
                time.sleep(0.1)
            self.profile = None


# --------------------------------------------------------------------------- #
# Conexão CDP                                                                   #
# --------------------------------------------------------------------------- #
class _Conn:
    """Uma conexão CDP no nível do navegador; comandos de aba vão com `sessionId`
    (sessões "flatten"). Respostas são casadas por id; eventos, por sessão."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._listeners: dict[str, list[Callable[[str, dict], None]]] = {}
        self._reader = asyncio.get_running_loop().create_task(self._read())
        self.closed = False

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut is not None and not fut.done():
                        if "error" in msg:
                            fut.set_exception(CDPError(msg["error"].get("message") or str(msg["error"])))
                        else:
                            fut.set_result(msg.get("result") or {})
                    continue
                for cb in list(self._listeners.get(msg.get("sessionId") or "", [])):
                    try:
                        cb(msg.get("method") or "", msg.get("params") or {})
                    except Exception:  # noqa: BLE001 - um ouvinte ruim não derruba a conexão
                        logger.exception("ouvinte CDP falhou")
        except Exception:  # noqa: BLE001 - conexão caiu
            pass
        finally:
            self.closed = True
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(CDPError("conexão com o navegador caiu"))
            self._pending.clear()

    async def call(self, method: str, params: dict | None = None, *, session: str | None = None,
                   timeout: float = _ACTION_TIMEOUT) -> dict:
        if self.closed:
            raise CDPError("conexão com o navegador caiu")
        n = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[n] = fut
        msg: dict[str, Any] = {"id": n, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        await self.ws.send(json.dumps(msg))
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(n, None)
            raise CDPError(f"o navegador não respondeu a {method} em {timeout:.0f}s") from None

    def listen(self, session: str, cb: Callable[[str, dict], None]) -> None:
        self._listeners.setdefault(session, []).append(cb)

    def forget(self, session: str) -> None:
        self._listeners.pop(session, None)

    async def close(self) -> None:
        self._reader.cancel()
        try:
            await self.ws.close()
        except Exception:  # noqa: BLE001
            pass


class _Session:
    __slots__ = ("key", "conn", "context_id", "session_id", "last_used", "closed", "_waiters")

    def __init__(self, key: str, conn: _Conn, context_id: str, session_id: str) -> None:
        self.key = key
        self.conn = conn
        self.context_id = context_id
        self.session_id = session_id
        self.last_used = time.time()
        self.closed = False
        self._waiters: list[tuple[str, asyncio.Future]] = []

    def touch(self) -> None:
        self.last_used = time.time()

    def expect(self, *events: str) -> asyncio.Future:
        """Futuro que resolve no PRÓXIMO destes eventos (registre antes de agir)."""
        fut = asyncio.get_running_loop().create_future()
        for ev in events:
            self._waiters.append((ev, fut))
        return fut

    def on_event(self, method: str) -> None:
        pendentes = []
        for ev, fut in self._waiters:
            if ev == method and not fut.done():
                fut.set_result(method)
            elif not fut.done():
                pendentes.append((ev, fut))
        self._waiters = pendentes

    async def call(self, method: str, params: dict | None = None, timeout: float = _ACTION_TIMEOUT) -> dict:
        return await self.conn.call(method, params, session=self.session_id, timeout=timeout)

    async def eval(self, expression: str, timeout: float = _ACTION_TIMEOUT) -> Any:
        r = await self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        }, timeout=timeout)
        if r.get("exceptionDetails"):
            det = r["exceptionDetails"]
            raise CDPError((det.get("exception") or {}).get("description") or det.get("text") or "erro de JS")
        return (r.get("result") or {}).get("value")


# --------------------------------------------------------------------------- #
# Driver                                                                        #
# --------------------------------------------------------------------------- #
class BrowserDriver:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._start_lock = threading.Lock()
        self._reaper_task: asyncio.Task | None = None
        # conexões por ENDPOINT (config do browser é por-usuário: cada um pode apontar
        # para um navegador diferente). Sessões são keyed por "endpoint\x00chat" para
        # nunca cruzarem entre endpoints.
        self._conns: dict[str, _Conn] = {}
        self._local = _LocalBrowser()
        self._local_ws = ""
        self._sessions: dict[str, _Session] = {}
        # guarda anti-SSRF aplicada a TODA requisição da página (a tool injeta a sua)
        self.url_guard: Callable[[str], bool] | None = None
        self._guard_cache: dict[str, tuple[float, bool]] = {}

    # ------------------------------- infra ---------------------------------- #
    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        with self._start_lock:
            if self._thread and self._thread.is_alive():
                return
            self._ready.clear()
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._run_loop, name="browser-driver", daemon=True)
            self._thread.start()
            self._ready.wait(timeout=5)

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.call_soon(self._ready.set)
        self._reaper_task = self._loop.create_task(self._reaper())
        self._loop.run_forever()

    def _submit(self, coro, timeout: float) -> Any:
        self._ensure_thread()
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)  # type: ignore[arg-type]
        return fut.result(timeout=timeout)

    async def _conn(self, endpoint: str) -> _Conn:
        c = self._conns.get(endpoint)
        if c is not None and not c.closed:
            return c
        import websockets

        if endpoint == LOCAL:
            # conexão caiu = o navegador local caiu: recomeça do zero (o PID do Popen
            # não serve de sinal — ver _LocalBrowser)
            self._local.stop()
            self._local_ws = await self._local.start()
            ws_url = self._local_ws
        else:
            ws_url = endpoint
        ws = await websockets.connect(ws_url, max_size=2**26, open_timeout=_ACTION_TIMEOUT)
        c = _Conn(ws)
        self._conns[endpoint] = c
        return c

    async def _get_session(self, endpoint: str, key: str) -> _Session:
        skey = f"{endpoint}\x00{key}"
        sess = self._sessions.get(skey)
        if sess is not None and not sess.closed and not sess.conn.closed:
            return sess
        if sess is not None:  # aba morreu → limpa
            self._sessions.pop(skey, None)
        conn = await self._conn(endpoint)
        # teto: se estourar, fecha a mais antiga ociosa
        if len(self._sessions) >= _MAX_SESSIONS:
            oldest = min(self._sessions.values(), key=lambda x: x.last_used)
            await self._close_session(oldest.key)
        ctx = (await conn.call("Target.createBrowserContext", {"disposeOnDetach": True}))["browserContextId"]
        target = (await conn.call("Target.createTarget",
                                  {"url": "about:blank", "browserContextId": ctx}))["targetId"]
        sid = (await conn.call("Target.attachToTarget", {"targetId": target, "flatten": True}))["sessionId"]
        sess = _Session(skey, conn, ctx, sid)
        conn.listen(sid, lambda method, params: self._on_event(sess, method, params))
        await sess.call("Page.enable")
        await sess.call("Runtime.enable")
        await sess.call("Network.setUserAgentOverride", {"userAgent": _UA})
        await sess.call("Emulation.setDeviceMetricsOverride",
                        {"width": 1280, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        if self.url_guard is not None:
            await sess.call("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})
        self._sessions[skey] = sess
        return sess

    def _on_event(self, sess: _Session, method: str, params: dict) -> None:
        if method == "Fetch.requestPaused":
            asyncio.get_running_loop().create_task(self._guard_request(sess, params))
            return
        if method in ("Inspector.detached", "Target.targetCrashed"):
            sess.closed = True
        sess.on_event(method)

    async def _guard_request(self, sess: _Session, params: dict) -> None:
        url = (params.get("request") or {}).get("url") or ""
        rid = params.get("requestId")
        try:
            if await self._allowed(url):
                await sess.call("Fetch.continueRequest", {"requestId": rid})
            else:
                logger.info("navegador: requisição barrada pela guarda de rede: %s", url[:200])
                await sess.call("Fetch.failRequest", {"requestId": rid, "errorReason": "AccessDenied"})
        except CDPError:
            pass  # aba fechou no meio

    async def _allowed(self, url: str) -> bool:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return p.scheme in ("data", "blob", "about")
        guard = self.url_guard
        if guard is None:
            return True
        chave = f"{p.scheme}://{(p.hostname or '').lower()}:{p.port or ''}"
        agora = time.monotonic()
        memo = self._guard_cache.get(chave)
        if memo is not None and agora - memo[0] < _GUARD_TTL:
            return memo[1]
        ok = bool(await asyncio.get_running_loop().run_in_executor(None, guard, url))
        self._guard_cache[chave] = (agora, ok)
        return ok

    async def _close_session(self, key: str) -> None:
        sess = self._sessions.pop(key, None)
        if sess is None:
            return
        sess.closed = True
        sess.conn.forget(sess.session_id)
        try:
            await sess.conn.call("Target.disposeBrowserContext", {"browserContextId": sess.context_id})
        except CDPError:
            pass

    async def _reaper(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = time.time()
            for key in [k for k, s in self._sessions.items() if now - s.last_used > _IDLE_TTL]:
                await self._close_session(key)

    # ------------------------------ estado ---------------------------------- #
    async def _state(self, sess: _Session) -> dict[str, Any]:
        try:
            st = await sess.eval(_STATE_JS) or {}
        except CDPError:
            st = {}
        return {
            "ok": True,
            "url": st.get("url") or "",
            "title": (st.get("title") or "")[:200],
            "text": (st.get("text") or "")[:_MAX_TEXT],
            "elements": st.get("elements") or [],
        }

    @staticmethod
    def _watch(sess: _Session) -> tuple[asyncio.Future, asyncio.Future]:
        """Registra os avisos de navegação ANTES de agir: numa página rápida o evento
        chega antes de a ação terminar, e um aviso registrado depois o perderia."""
        return sess.expect("Page.frameStartedLoading"), sess.expect("Page.domContentEventFired")

    async def _settle(self, started: asyncio.Future, loaded: asyncio.Future) -> None:
        """Depois de clique/Enter: se uma navegação começou, espera o DOM dela."""
        try:
            await asyncio.wait_for(asyncio.shield(started), 0.7)
        except asyncio.TimeoutError:
            await asyncio.sleep(0.15)  # nem todo clique navega; dá tempo ao JS da página
            return
        try:
            await asyncio.wait_for(loaded, _NAV_TIMEOUT)
        except asyncio.TimeoutError:
            pass

    # ------------------------------ ações ----------------------------------- #
    async def _act_goto(self, endpoint: str, key: str, url: str) -> dict[str, Any]:
        sess = await self._get_session(endpoint, key)
        loaded = sess.expect("Page.domContentEventFired")
        r = await sess.call("Page.navigate", {"url": url}, timeout=_NAV_TIMEOUT)
        if r.get("errorText"):
            raise CDPError(f"falha ao abrir {url}: {r['errorText']}")
        if r.get("loaderId"):  # navegação de documento (não âncora na mesma página)
            try:
                await asyncio.wait_for(loaded, _NAV_TIMEOUT)
            except asyncio.TimeoutError:
                pass
        sess.touch()
        return await self._state(sess)

    async def _act_read(self, endpoint: str, key: str) -> dict[str, Any]:
        sess = self._require(endpoint, key)
        sess.touch()
        return await self._state(sess)

    async def _act_click(self, endpoint: str, key: str, target: str) -> dict[str, Any]:
        sess = self._require(endpoint, key)
        pos = await sess.eval(_CLICK_JS % (json.dumps(target), json.dumps(_looks_css(target))))
        if not isinstance(pos, dict) or pos.get("error"):
            raise CDPError((pos or {}).get("error") or f"element not found: {target}")
        started, loaded = self._watch(sess)
        base = {"x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1}
        await sess.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": pos["x"], "y": pos["y"]})
        await sess.call("Input.dispatchMouseEvent", {"type": "mousePressed", **base})
        await sess.call("Input.dispatchMouseEvent", {"type": "mouseReleased", **base})
        await self._settle(started, loaded)
        sess.touch()
        return await self._state(sess)

    async def _act_type(self, endpoint: str, key: str, target: str, text: str, submit: bool) -> dict[str, Any]:
        sess = self._require(endpoint, key)
        r = await sess.eval(_FIELD_JS % (json.dumps(target), json.dumps(bool(target) and _looks_css(target))))
        if not isinstance(r, dict) or r.get("error"):
            raise CDPError((r or {}).get("error") or f"field not found: {target}")
        if text:
            # insertText gera os eventos de digitação reais (React/Vue enxergam) e
            # substitui a seleção feita pelo _FIELD_JS
            await sess.call("Input.insertText", {"text": text})
        else:
            for tipo in ("keyDown", "keyUp"):
                await sess.call("Input.dispatchKeyEvent", {"type": tipo, "key": "Backspace",
                                                           "code": "Backspace", "windowsVirtualKeyCode": 8})
        if submit:
            started, loaded = self._watch(sess)
            enter = {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13}
            await sess.call("Input.dispatchKeyEvent", {"type": "keyDown", "text": "\r", **enter})
            await sess.call("Input.dispatchKeyEvent", {"type": "keyUp", **enter})
            await self._settle(started, loaded)
        sess.touch()
        return await self._state(sess)

    async def _act_scroll(self, endpoint: str, key: str, direction: str) -> dict[str, Any]:
        sess = self._require(endpoint, key)
        dy = {"up": -800, "down": 800, "top": -100000, "bottom": 100000}.get(direction, 800)
        await sess.eval(f"window.scrollBy(0, {int(dy)})")
        sess.touch()
        return await self._state(sess)

    async def _act_back(self, endpoint: str, key: str) -> dict[str, Any]:
        sess = self._require(endpoint, key)
        hist = await sess.call("Page.getNavigationHistory")
        idx = int(hist.get("currentIndex") or 0)
        entries = hist.get("entries") or []
        if idx <= 0 or idx >= len(entries):
            raise CDPError("no previous page in this tab")
        loaded = sess.expect("Page.domContentEventFired")
        await sess.call("Page.navigateToHistoryEntry", {"entryId": entries[idx - 1]["id"]})
        try:
            await asyncio.wait_for(loaded, _NAV_TIMEOUT)
        except asyncio.TimeoutError:
            pass
        sess.touch()
        return await self._state(sess)

    async def _act_screenshot(self, endpoint: str, key: str) -> bytes:
        sess = self._require(endpoint, key)
        sess.touch()
        r = await sess.call("Page.captureScreenshot", {"format": "png"}, timeout=_NAV_TIMEOUT)
        return base64.b64decode(r["data"])

    def _require(self, endpoint: str, key: str) -> _Session:
        sess = self._sessions.get(f"{endpoint}\x00{key}")
        if sess is None or sess.closed or sess.conn.closed:
            raise CDPError("no open page — use action 'goto' with a URL first")
        return sess

    async def _act_probe(self, endpoint: str) -> None:
        """Conecta e fecha um contexto — usado pelo 'Testar conexão'."""
        conn = await self._conn(endpoint)
        ctx = (await conn.call("Target.createBrowserContext", {}))["browserContextId"]
        await conn.call("Target.disposeBrowserContext", {"browserContextId": ctx})

    # --------------------------- API síncrona ------------------------------- #
    def goto(self, endpoint: str, key: str, url: str) -> dict[str, Any]:
        return self._submit(self._act_goto(endpoint, key, url), timeout=_NAV_TIMEOUT + 25)

    def read(self, endpoint: str, key: str) -> dict[str, Any]:
        return self._submit(self._act_read(endpoint, key), timeout=_ACTION_TIMEOUT + 10)

    def click(self, endpoint: str, key: str, target: str) -> dict[str, Any]:
        return self._submit(self._act_click(endpoint, key, target), timeout=_NAV_TIMEOUT + 25)

    def type(self, endpoint: str, key: str, target: str, text: str, submit: bool) -> dict[str, Any]:
        return self._submit(self._act_type(endpoint, key, target, text, submit), timeout=_NAV_TIMEOUT + 25)

    def scroll(self, endpoint: str, key: str, direction: str) -> dict[str, Any]:
        return self._submit(self._act_scroll(endpoint, key, direction), timeout=_ACTION_TIMEOUT + 10)

    def back(self, endpoint: str, key: str) -> dict[str, Any]:
        return self._submit(self._act_back(endpoint, key), timeout=_NAV_TIMEOUT + 25)

    def screenshot(self, endpoint: str, key: str) -> bytes:
        return self._submit(self._act_screenshot(endpoint, key), timeout=_NAV_TIMEOUT + 10)

    def close(self, endpoint: str, key: str) -> dict[str, Any]:
        self._submit(self._close_session(f"{endpoint}\x00{key}"), timeout=15)
        return {"ok": True, "closed": True}

    def probe(self, endpoint: str) -> None:
        """Testa a conexão (levanta em erro). Timeout curto para o botão de teste."""
        self._submit(self._act_probe(endpoint), timeout=30)

    def shutdown(self) -> None:
        """Fecha tudo e para o loop (chamado no lifespan)."""
        if not self._loop or not self._thread or not self._thread.is_alive():
            self._local.stop()
            return

        async def _teardown() -> None:
            if self._reaper_task is not None:
                self._reaper_task.cancel()
            for key in list(self._sessions):
                await self._close_session(key)
            local = self._conns.get(LOCAL)
            if local is not None and not local.closed:
                try:
                    await local.call("Browser.close", timeout=5)
                except CDPError:
                    pass
            for ep in list(self._conns):
                await self._conns.pop(ep).close()
            self._local.stop()
        try:
            asyncio.run_coroutine_threadsafe(_teardown(), self._loop).result(timeout=20)
        except Exception:  # noqa: BLE001
            self._local.stop()
        self._loop.call_soon_threadsafe(self._loop.stop)


# singleton
driver = BrowserDriver()
