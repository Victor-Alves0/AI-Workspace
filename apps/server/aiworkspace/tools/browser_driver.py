"""Driver do Navegador headless (tool `web.browser.use`).

O Chromium roda num container opt-in (browserless, `--profile browser`); aqui a gente
o dirige por Playwright via CDP. Uma ABA VIVA por conversa persiste entre chamadas de
tool (login, formulários, fluxos de vários passos).

Ponte estado × tool síncrona: as tools da SIFT são funções SÍNCRONAS (threadpool) e
usam seu próprio loop efêmero — um objeto async do Playwright não sobrevive a isso. Por
isso este driver é um SINGLETON com uma THREAD + event loop PRÓPRIOS que são donos dos
objetos Playwright; as tools submetem corrotinas via `run_coroutine_threadsafe`.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# tetos anti-abuso
_MAX_SESSIONS = 8
_IDLE_TTL = 300.0          # fecha aba ociosa após 5 min
_NAV_TIMEOUT_MS = 30_000   # navegação (goto/click que troca de página)
_ACTION_TIMEOUT_MS = 15_000
_MAX_TEXT = 6000           # texto legível devolvido por ação
_MAX_ELEMENTS = 40         # elementos interativos listados
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# JS que coleta os elementos interativos VISÍVEIS (links/botões/campos) com o texto
# acessível — a IA clica/digita mirando esse texto (ou um seletor CSS).
_ELEMENTS_JS = """() => {
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
  return out;
}""" % _MAX_ELEMENTS


class _Session:
    __slots__ = ("key", "context", "page", "last_used")

    def __init__(self, key: str, context, page):
        self.key = key
        self.context = context
        self.page = page
        self.last_used = time.time()

    def touch(self) -> None:
        self.last_used = time.time()


class BrowserDriver:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._start_lock = threading.Lock()
        self._pw = None
        self._reaper_task: asyncio.Task | None = None
        # conexões por ENDPOINT (config do browser é por-usuário: cada um pode
        # apontar para um browserless diferente). Sessões são keyed por
        # "endpoint\x00chat" para nunca cruzarem entre endpoints.
        self._browsers: dict[str, Any] = {}
        self._sessions: dict[str, _Session] = {}

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

    async def _ensure_browser(self, endpoint: str):
        """Conecta (ou reusa) o browser para ESTE endpoint (ws_url[?token=])."""
        b = self._browsers.get(endpoint)
        if b is not None and b.is_connected():
            return b
        from playwright.async_api import async_playwright
        if self._pw is None:
            self._pw = await async_playwright().start()
        b = await self._pw.chromium.connect_over_cdp(endpoint)
        self._browsers[endpoint] = b
        return b

    async def _get_session(self, endpoint: str, key: str) -> _Session:
        skey = f"{endpoint}\x00{key}"
        sess = self._sessions.get(skey)
        if sess is not None and not sess.page.is_closed():
            return sess
        if sess is not None:  # aba morreu → limpa
            self._sessions.pop(skey, None)
        browser = await self._ensure_browser(endpoint)
        # teto: se estourar, fecha a mais antiga ociosa
        if len(self._sessions) >= _MAX_SESSIONS:
            oldest = min(self._sessions.values(), key=lambda x: x.last_used)
            await self._close_session(oldest.key)
        ctx = await browser.new_context(user_agent=_UA, viewport={"width": 1280, "height": 900})
        ctx.set_default_timeout(_ACTION_TIMEOUT_MS)
        page = await ctx.new_page()
        sess = _Session(skey, ctx, page)
        self._sessions[skey] = sess
        return sess

    async def _close_session(self, key: str) -> None:
        sess = self._sessions.pop(key, None)
        if sess is not None:
            try:
                await sess.context.close()
            except Exception:  # noqa: BLE001
                pass

    async def _reaper(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = time.time()
            for key in [k for k, s in self._sessions.items() if now - s.last_used > _IDLE_TTL]:
                await self._close_session(key)

    # ------------------------------ estado ---------------------------------- #
    async def _state(self, page) -> dict[str, Any]:
        try:
            title = await page.title()
        except Exception:  # noqa: BLE001
            title = ""
        try:
            text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:  # noqa: BLE001
            text = ""
        try:
            elements = await page.evaluate(_ELEMENTS_JS)
        except Exception:  # noqa: BLE001
            elements = []
        return {
            "ok": True,
            "url": page.url,
            "title": (title or "")[:200],
            "text": (text or "")[:_MAX_TEXT],
            "elements": elements,
        }

    @staticmethod
    def _looks_css(target: str) -> bool:
        t = target.strip()
        return t.startswith((".", "#", "[")) or ">" in t or (" " not in t and any(c in t for c in ".#[]>"))

    def _locator(self, page, target: str):
        if self._looks_css(target):
            return page.locator(target).first
        return page.get_by_text(target, exact=False).first

    def _input_locator(self, page, target: str):
        if not target:
            return page.locator("input:visible, textarea:visible").first
        if self._looks_css(target):
            return page.locator(target).first
        # tenta label → placeholder → texto genérico
        return page.get_by_label(target).or_(page.get_by_placeholder(target)).first

    # ------------------------------ ações ----------------------------------- #
    async def _act_goto(self, endpoint: str, key: str, url: str) -> dict[str, Any]:
        sess = await self._get_session(endpoint, key)
        await sess.page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
        sess.touch()
        return await self._state(sess.page)

    async def _act_read(self, endpoint: str, key: str) -> dict[str, Any]:
        sess = self._require(endpoint, key)
        sess.touch()
        return await self._state(sess.page)

    async def _act_click(self, endpoint: str, key: str, target: str) -> dict[str, Any]:
        sess = self._require(endpoint, key)
        await self._locator(sess.page, target).click(timeout=_ACTION_TIMEOUT_MS)
        try:
            await sess.page.wait_for_load_state("domcontentloaded", timeout=_NAV_TIMEOUT_MS)
        except Exception:  # noqa: BLE001 - nem todo clique navega
            pass
        sess.touch()
        return await self._state(sess.page)

    async def _act_type(self, endpoint: str, key: str, target: str, text: str, submit: bool) -> dict[str, Any]:
        sess = self._require(endpoint, key)
        loc = self._input_locator(sess.page, target)
        await loc.fill(text, timeout=_ACTION_TIMEOUT_MS)
        if submit:
            await loc.press("Enter")
            try:
                await sess.page.wait_for_load_state("domcontentloaded", timeout=_NAV_TIMEOUT_MS)
            except Exception:  # noqa: BLE001
                pass
        sess.touch()
        return await self._state(sess.page)

    async def _act_scroll(self, endpoint: str, key: str, direction: str) -> dict[str, Any]:
        sess = self._require(endpoint, key)
        dy = {"up": -800, "down": 800, "top": -100000, "bottom": 100000}.get(direction, 800)
        await sess.page.evaluate("(d) => window.scrollBy(0, d)", dy)
        sess.touch()
        return await self._state(sess.page)

    async def _act_back(self, endpoint: str, key: str) -> dict[str, Any]:
        sess = self._require(endpoint, key)
        await sess.page.go_back(wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
        sess.touch()
        return await self._state(sess.page)

    async def _act_screenshot(self, endpoint: str, key: str) -> bytes:
        sess = self._require(endpoint, key)
        sess.touch()
        return await sess.page.screenshot(type="png", full_page=False)

    def _require(self, endpoint: str, key: str) -> _Session:
        sess = self._sessions.get(f"{endpoint}\x00{key}")
        if sess is None or sess.page.is_closed():
            raise RuntimeError("no open page — use action 'goto' with a URL first")
        return sess

    async def _act_probe(self, endpoint: str) -> None:
        """Conecta e fecha um contexto — usado pelo 'Testar conexão'."""
        b = await self._ensure_browser(endpoint)
        ctx = await b.new_context()
        await ctx.close()

    # --------------------------- API síncrona ------------------------------- #
    def goto(self, endpoint: str, key: str, url: str) -> dict[str, Any]:
        return self._submit(self._act_goto(endpoint, key, url), timeout=_NAV_TIMEOUT_MS / 1000 + 10)

    def read(self, endpoint: str, key: str) -> dict[str, Any]:
        return self._submit(self._act_read(endpoint, key), timeout=_ACTION_TIMEOUT_MS / 1000 + 10)

    def click(self, endpoint: str, key: str, target: str) -> dict[str, Any]:
        return self._submit(self._act_click(endpoint, key, target), timeout=_NAV_TIMEOUT_MS / 1000 + 10)

    def type(self, endpoint: str, key: str, target: str, text: str, submit: bool) -> dict[str, Any]:
        return self._submit(self._act_type(endpoint, key, target, text, submit), timeout=_NAV_TIMEOUT_MS / 1000 + 10)

    def scroll(self, endpoint: str, key: str, direction: str) -> dict[str, Any]:
        return self._submit(self._act_scroll(endpoint, key, direction), timeout=_ACTION_TIMEOUT_MS / 1000 + 10)

    def back(self, endpoint: str, key: str) -> dict[str, Any]:
        return self._submit(self._act_back(endpoint, key), timeout=_NAV_TIMEOUT_MS / 1000 + 10)

    def screenshot(self, endpoint: str, key: str) -> bytes:
        return self._submit(self._act_screenshot(endpoint, key), timeout=_ACTION_TIMEOUT_MS / 1000 + 10)

    def close(self, endpoint: str, key: str) -> dict[str, Any]:
        self._submit(self._close_session(f"{endpoint}\x00{key}"), timeout=15)
        return {"ok": True, "closed": True}

    def probe(self, endpoint: str) -> None:
        """Testa a conexão (levanta em erro). Timeout curto para o botão de teste."""
        self._submit(self._act_probe(endpoint), timeout=20)

    def shutdown(self) -> None:
        """Fecha tudo e para o loop (chamado no lifespan)."""
        if not self._loop or not self._thread or not self._thread.is_alive():
            return
        async def _teardown() -> None:
            if self._reaper_task is not None:
                self._reaper_task.cancel()
            for key in list(self._sessions):
                await self._close_session(key)
            for ep in list(self._browsers):
                try:
                    await self._browsers.pop(ep).close()
                except Exception:  # noqa: BLE001
                    pass
            if self._pw is not None:
                try:
                    await self._pw.stop()
                except Exception:  # noqa: BLE001
                    pass
        try:
            asyncio.run_coroutine_threadsafe(_teardown(), self._loop).result(timeout=20)
        except Exception:  # noqa: BLE001
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)


# singleton
driver = BrowserDriver()
