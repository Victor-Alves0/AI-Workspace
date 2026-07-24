"""Pool rotativo de cookies + headers realistas para o yt-dlp — reduz bloqueio
(bot-check / HTTP 429) do YouTube e afins na tool `media.video.transcribe`.

Duas defesas, ambas com degradação suave (nada quebra se nada for configurado):

  1. HEADERS realistas — User-Agent de navegador recente (rotacionado) + Accept-Language.
     SEMPRE aplicados; não precisam de configuração.
  2. COOKIES rotativos — se `TRANSCRIBE_COOKIES_DIR` aponta p/ uma pasta de arquivos
     `*.txt` (cookies Netscape), CADA arquivo é uma IDENTIDADE. `acquire_cookie()`
     entrega a menos-recente-usada FORA de cooldown; a que tomar bloqueio vai p/
     cooldown (com backoff), então não batemos sempre na mesma tecla. Sem pasta/arquivos,
     roda só com headers.

Thread-safe: o yt-dlp roda em threadpool, então várias transcrições podem pegar
identidades ao mesmo tempo. As helpers são puras/testáveis (sem rede).
"""

from __future__ import annotations

import glob
import logging
import os
import random
import threading
import time
from dataclasses import dataclass

from ..config import get_settings

logger = logging.getLogger(__name__)

# User-Agents de navegadores reais e recentes. Rotacionar dilui a "impressão digital"
# fixa que um scraper deixa. Manter atualizado é rotina (como o próprio yt-dlp).
_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
)

# Trechos que denunciam um BLOQUEIO (rotacionar a identidade pode resolver) — em
# oposição a erros que a rotação NÃO resolve (URL inválida, vídeo privado/removido).
_BLOCK_MARKERS = (
    "sign in to confirm",
    "confirm you're not a bot",
    "confirm you are not a bot",
    "http error 429",
    "too many requests",
    "this content isn't available",
    "who has blocked it",
    "video is not available",
)

_MAX_BACKOFF = 6  # teto do multiplicador de cooldown (falhas consecutivas)
_RESCAN_SECONDS = 30.0  # re-lê a pasta no máx a cada 30s (cookies podem ser adicionados)


def base_headers() -> dict[str, str]:
    """Headers de navegador realistas, com User-Agent sorteado a cada chamada."""
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept-Language": "en-US,en;q=0.9",
    }


def looks_blocked(exc: BaseException | str) -> bool:
    """True se a mensagem do yt-dlp indica bloqueio/rate-limit (não um erro terminal)."""
    msg = str(exc).lower()
    return any(m in msg for m in _BLOCK_MARKERS)


def apply_evasion(base_opts: dict, cookie: str | None) -> dict:
    """Copia `base_opts` acrescentando headers realistas e (se houver) o cookiefile.
    Não muta o dict recebido — headers e cookie mudam a cada tentativa."""
    opts = dict(base_opts)
    headers = dict(opts.get("http_headers") or {})
    headers.update(base_headers())
    opts["http_headers"] = headers
    if cookie:
        opts["cookiefile"] = cookie
    return opts


@dataclass
class _Identity:
    path: str
    last_used: float = 0.0
    blocked_until: float = 0.0
    fails: int = 0


class _CookiePool:
    """Rotaciona arquivos de cookie por menos-recente-uso, com cooldown por bloqueio."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_path: dict[str, _Identity] = {}
        self._dir: str | None = None
        self._scanned_at = 0.0

    def _rescan_locked(self, directory: str) -> None:
        now = time.time()
        if directory == self._dir and now - self._scanned_at < _RESCAN_SECONDS:
            return
        files = sorted(glob.glob(os.path.join(directory, "*.txt")))
        # preserva o estado (last_used/cooldown) dos arquivos que continuam existindo
        self._by_path = {p: self._by_path.get(p, _Identity(path=p)) for p in files}
        self._dir = directory
        self._scanned_at = now

    def acquire(self) -> str | None:
        directory = (get_settings().transcribe_cookies_dir or "").strip()
        if not directory or not os.path.isdir(directory):
            return None
        with self._lock:
            self._rescan_locked(directory)
            if not self._by_path:
                return None
            now = time.time()
            ready = [i for i in self._by_path.values() if i.blocked_until <= now]
            # todas em cooldown? usa a que sai do banco primeiro (menos ruim que nada)
            pool = ready or list(self._by_path.values())
            chosen = min(pool, key=lambda i: (i.last_used, i.blocked_until))
            chosen.last_used = now
            return chosen.path

    def report(self, path: str | None, *, blocked: bool) -> None:
        if not path:
            return
        cooldown = float(get_settings().transcribe_cookie_cooldown_seconds)
        with self._lock:
            ident = self._by_path.get(path)
            if ident is None:
                return
            if blocked:
                ident.fails += 1
                secs = cooldown * min(ident.fails, _MAX_BACKOFF)
                ident.blocked_until = time.time() + secs
                logger.warning(
                    "Cookie %s tomou bloqueio; em cooldown por ~%ds (falhas=%d)",
                    os.path.basename(path), int(secs), ident.fails,
                )
            else:
                ident.fails = 0
                ident.blocked_until = 0.0

    def count(self) -> int:
        directory = (get_settings().transcribe_cookies_dir or "").strip()
        if not directory or not os.path.isdir(directory):
            return 0
        with self._lock:
            self._rescan_locked(directory)
            return len(self._by_path)


_POOL = _CookiePool()


def acquire_cookie() -> str | None:
    """Caminho do cookie a usar nesta tentativa, ou None se nenhum configurado."""
    return _POOL.acquire()


def report_cookie(path: str | None, *, blocked: bool) -> None:
    """Realimenta o resultado: sucesso zera o cooldown; bloqueio bane com backoff."""
    _POOL.report(path, blocked=blocked)


def cookie_count() -> int:
    """Quantas identidades de cookie estão disponíveis (0 = só headers)."""
    return _POOL.count()


def reset() -> None:
    """Zera o estado do pool (para testes hérmeticos)."""
    global _POOL
    _POOL = _CookiePool()
