"""Transcrição de vídeo/áudio a partir de uma URL — YouTube e ~1800 outros sites
(Vimeo, TikTok, notícias, podcasts, links diretos de mídia…) via **yt-dlp**.

Duas estratégias, em ordem de custo:
  1. LEGENDAS: se o vídeo tem captions (próprias ou automáticas), baixa e converte
     em texto — rápido, sem GPU, sem STT.
  2. FALA (fallback): sem legendas, baixa o áudio (yt-dlp), recomprime p/ 16 kHz mono
     e fatia em pedaços abaixo do teto do Whisper (ffmpeg), e transcreve cada pedaço
     pelo STT do usuário (conexão de Voz Local → provedor global), concatenando.

Nunca levanta p/ fora — devolve `{"error": ...}` para a tool repassar.

As funções de I/O externo (`_extract_info`, `_download_audio`, `_segment_audio`,
`_http_get_text`, `_stt_one`) são costuras finas, isoladas de propósito para os
testes hérmeticos as substituírem sem rede/binários.
"""

from __future__ import annotations

import glob
import logging
import os
import re
import subprocess
import tempfile
import time
from typing import Any

import httpx

from . import ytdlp_pool

logger = logging.getLogger(__name__)

# teto do Whisper é 25 MB; fatiamos bem abaixo p/ folga (headers multipart etc.)
_STT_LIMIT = 24 * 1024 * 1024
# fatia de ~10 min em 16 kHz mono mp3 48 kbps ≈ 3,6 MB (folgado abaixo do teto);
# fatiar por TEMPO (não por bytes) mantém as fronteiras em silêncio/fala natural.
_SEGMENT_SECONDS = 600
# limite de duração p/ conter custo/tempo do caminho de FALA (legendas não têm limite).
_MAX_SPEECH_DURATION = 4 * 3600
# preferência de formato de legenda: json3 (YouTube, limpo) → vtt (universal) → resto.
_SUB_FORMAT_PREF = ("json3", "vtt", "srv3", "srv1", "ttml")
# teto de faixas de legenda testadas antes de desistir (o YouTube expõe ~157 idiomas
# auto-traduzidos; sem teto, um vídeo sem legenda real viraria 157 downloads).
_MAX_SUB_TRIES = 4

# Cache em processo do transcript COMPLETO por (url, lang). O modelo tende a
# re-chamar a tool aumentando `max_chars` para "pegar o resto" (observado: 4 chamadas
# num turno, 3000→5000→5800→5822) — sem cache, cada uma re-baixava tudo. Com cache a
# repetição custa ~0 e o recorte é local.
_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_CACHE_TTL = 900.0  # 15 min
_CACHE_MAX = 8


# --------------------------------------------------------------------------- #
# Costuras de I/O externo (substituíveis nos testes)
# --------------------------------------------------------------------------- #
def _ytdlp_attempts() -> int:
    """Quantas tentativas rotacionando identidade. 0/1 cookie → 1 (rotacionar não muda
    nada); ≥2 → até 4 (cada tentativa pega uma identidade diferente ao tomar bloqueio)."""
    n = ytdlp_pool.cookie_count()
    return 1 if n <= 1 else min(n, 4)


def _run_ytdlp(base_opts: dict[str, Any], run: Any) -> Any:
    """Roda `run(ydl)` sob yt-dlp com headers realistas + cookie rotativo, re-tentando
    com OUTRA identidade quando a mensagem indica bloqueio (bot-check/429). Erros
    terminais (URL inválida, vídeo privado…) propagam na hora — rotação não os resolve."""
    import yt_dlp

    attempts = _ytdlp_attempts()
    last_exc: Exception | None = None
    for i in range(attempts):
        cookie = ytdlp_pool.acquire_cookie()
        opts = ytdlp_pool.apply_evasion(base_opts, cookie)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                result = run(ydl)
            ytdlp_pool.report_cookie(cookie, blocked=False)
            return result
        except Exception as exc:  # noqa: BLE001 - DownloadError/ExtractorError e afins
            last_exc = exc
            blocked = ytdlp_pool.looks_blocked(exc)
            ytdlp_pool.report_cookie(cookie, blocked=blocked)
            if not blocked or i == attempts - 1:
                raise
            logger.info(
                "yt-dlp bloqueado (tentativa %d/%d); rotacionando identidade", i + 1, attempts
            )
    if last_exc is not None:  # defensivo: attempts>=1 sempre retorna ou levanta acima
        raise last_exc
    return None


def _extract_info(url: str) -> dict[str, Any]:
    """Metadados do yt-dlp (título, duração, legendas) SEM baixar o vídeo."""
    base = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "extract_flat": False,
    }
    return _run_ytdlp(base, lambda ydl: ydl.extract_info(url, download=False) or {})


def _download_audio(url: str, tmpdir: str) -> str | None:
    """Baixa a melhor faixa de áudio p/ `tmpdir`; devolve o caminho do arquivo."""
    base = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "format": "bestaudio/best",
        "outtmpl": os.path.join(tmpdir, "audio.%(ext)s"),
    }
    _run_ytdlp(base, lambda ydl: ydl.download([url]))
    files = [p for p in glob.glob(os.path.join(tmpdir, "audio.*")) if os.path.isfile(p)]
    return files[0] if files else None


def _segment_audio(path: str, tmpdir: str) -> list[str]:
    """Recomprime p/ 16 kHz mono mp3 48 kbps e fatia em pedaços de `_SEGMENT_SECONDS`.
    Um só arquivo se o vídeo for curto. Requer ffmpeg no PATH (imagem do server)."""
    out = os.path.join(tmpdir, "seg%03d.mp3")
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", path, "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "libmp3lame", "-b:a", "48k",
            "-f", "segment", "-segment_time", str(_SEGMENT_SECONDS),
            out,
        ],
        check=True,
        timeout=1800,
    )
    return sorted(glob.glob(os.path.join(tmpdir, "seg*.mp3")))


async def _http_get_text(url: str) -> str:
    """Baixa uma legenda (vtt/json3) como texto, com headers de navegador realistas."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
        r = await client.get(url, headers=ytdlp_pool.base_headers())
    r.raise_for_status()
    return r.text


async def _stt_one(cand: dict[str, str], filename: str, audio: bytes) -> str | None:
    """Uma tentativa de transcrição OpenAI-compat. None quando o servidor não faz
    STT (404/405/501 — ex.: Kokoro) ou está fora do ar; texto quando transcreve."""
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(
                f"{cand['base_url']}/audio/transcriptions",
                headers={"Authorization": f"Bearer {cand['api_key']}"},
                files={"file": (filename, audio, "audio/mpeg")},
                data={"model": cand["model"]},
            )
    except httpx.HTTPError:
        return None
    if resp.status_code in (404, 405, 501):
        return None
    if resp.status_code != 200:
        raise RuntimeError(f"STT HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        return (resp.json() or {}).get("text") or ""
    except ValueError:
        return resp.text


# --------------------------------------------------------------------------- #
# Parsing de legendas
# --------------------------------------------------------------------------- #
_VTT_TS = re.compile(r"^\d{1,2}:\d{2}:\d{2}[.,]\d{3}\s*-->\s")
_TAG = re.compile(r"<[^>]+>")


def _vtt_to_text(vtt: str) -> str:
    """WEBVTT/SRT → texto corrido. Remove cabeçalho, timestamps, tags e a
    DUPLICAÇÃO rolante das legendas automáticas (linhas repetidas em sequência)."""
    lines: list[str] = []
    for raw in vtt.splitlines():
        s = raw.strip()
        if not s or s.upper().startswith("WEBVTT") or s.startswith("NOTE"):
            continue
        if _VTT_TS.match(s) or "-->" in s:
            continue
        if s.isdigit():  # índice de cue do SRT
            continue
        s = _TAG.sub("", s).strip()
        if not s:
            continue
        if lines and lines[-1] == s:  # dedup rolante das auto-captions
            continue
        lines.append(s)
    # dedup adicional: janela curta (auto-captions repetem a última linha do bloco anterior)
    out: list[str] = []
    for s in lines:
        if s in out[-2:]:
            continue
        out.append(s)
    return " ".join(out)


def _json3_to_text(data: Any) -> str:
    """Formato json3 do YouTube: events[].segs[].utf8 concatenados."""
    import json

    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return ""
    # os segs de UM evento são pedaços da mesma frase (juntam direto); eventos
    # diferentes são cues distintas (juntam com espaço, senão colam palavras).
    lines: list[str] = []
    for ev in (data or {}).get("events") or []:
        seg_text = "".join(
            seg.get("utf8", "") for seg in ev.get("segs") or []
            if seg.get("utf8") and seg["utf8"] != "\n"
        ).strip()
        if seg_text:
            lines.append(seg_text)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def _subtitle_candidates(info: dict[str, Any], lang: str) -> list[tuple[str, str, str]]:
    """Legendas candidatas EM ORDEM de preferência: (lang, ext, url).

    Ordem de idioma: o pedido → o ORIGINAL do vídeo (`info['language']`) → inglês/
    português → o resto. O original vem cedo de propósito: num vídeo em pt o YouTube
    oferece ~157 faixas AUTO-TRADUZIDAS e a de 'en' costuma vir VAZIA — fixar 'en'
    no topo fazia a transcrição falhar e cair no STT (bug real observado).

    Devolve uma LISTA (não a "melhor") porque uma faixa pode baixar vazia; quem chama
    tenta a próxima em vez de desistir e ir para o caminho caro de fala.
    """
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    original = (info.get("language") or "").strip()

    pref: list[str] = []
    for code in (lang, original, "en", "pt"):
        if code and code not in pref:
            pref.append(code)
    # variantes regionais dos preferidos (pt-BR, en-US…) e depois o resto
    available = [*manual.keys(), *auto.keys()]
    for code in list(pref):
        for k in available:
            if k.startswith(f"{code}-") and k not in pref:
                pref.append(k)
    ordered = [c for c in pref if c in manual or c in auto]
    ordered += [k for k in available if k not in pref]

    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for source in (manual, auto):  # manuais primeiro (mais precisas que as automáticas)
        for code in ordered:
            fmts = source.get(code)
            if not fmts:
                continue
            by_ext = {f.get("ext"): f.get("url") for f in fmts if f.get("url")}
            exts = [e for e in _SUB_FORMAT_PREF if by_ext.get(e)]
            exts += [f.get("ext") for f in fmts if f.get("url") and f.get("ext") not in exts]
            for ext in exts:
                url = by_ext.get(ext) or next(
                    (f["url"] for f in fmts if f.get("ext") == ext and f.get("url")), None
                )
                if url and (code, ext) not in seen:
                    seen.add((code, ext))
                    out.append((code, ext or "vtt", url))
    return out


# --------------------------------------------------------------------------- #
# Orquestração
# --------------------------------------------------------------------------- #
def _clip(text: str, cap: int) -> dict[str, Any]:
    """Recorta o transcript e descreve o corte SEM convidar a uma re-chamada.

    A nota anterior dizia "call again with a larger max_chars" e o modelo obedecia
    em cascata (3000→5000→5800→5822 num único turno). Agora ela diz que o trecho já
    basta para resumir e que só vale re-chamar se o usuário pedir o texto EXATO —
    e nesse caso já informa o valor único a usar (evita a escadinha)."""
    full = len(text)
    clipped = text[:cap]
    out: dict[str, Any] = {"text": clipped, "chars": len(clipped), "full_chars": full}
    if full > cap:
        out["truncated"] = True
        out["note"] = (
            f"Showing the first {cap} of {full} chars. This is normally enough to summarize "
            "or answer about the video. Do NOT re-call this tool just to get a bit more; only "
            f"if the user needs the VERBATIM full text, call once with max_chars={full}."
        )
    return out


async def _via_captions(info: dict[str, Any], lang: str) -> tuple[str, str] | None:
    """(texto, lang) da PRIMEIRA legenda candidata que render texto, ou None.

    Tenta várias faixas (até `_MAX_SUB_TRIES`): uma faixa auto-traduzida pode baixar
    vazia, e desistir na primeira jogava o turno no caminho caro de fala (STT)."""
    tried = 0
    for code, ext, url in _subtitle_candidates(info, lang):
        if tried >= _MAX_SUB_TRIES:
            break
        tried += 1
        try:
            raw = await _http_get_text(url)
        except Exception as exc:  # noqa: BLE001
            logger.info("Legenda %s/%s falhou ao baixar (%s); tentando a próxima", code, ext, exc)
            continue
        text = (_json3_to_text(raw) if ext == "json3" else _vtt_to_text(raw)).strip()
        if text:
            return text, code
        logger.info("Legenda %s/%s veio vazia; tentando a próxima", code, ext)
    return None


async def _via_speech(url: str, info: dict[str, Any], stt_candidates: list[dict]) -> dict[str, Any]:
    """Baixa o áudio, fatia e transcreve pelo STT. Devolve dict de resultado/erro."""
    if not stt_candidates:
        return {"error": (
            "O vídeo não tem legendas e não há servidor de transcrição (STT) configurado. "
            "Configure uma conexão de Voz Local ou a chave do provedor de voz em "
            "Configurações → Voz para transcrever a fala."
        )}
    duration = info.get("duration") or 0
    if duration and duration > _MAX_SPEECH_DURATION:
        return {"error": (
            f"Mídia muito longa p/ transcrever pela fala ({int(duration) // 60} min; teto "
            f"{_MAX_SPEECH_DURATION // 3600} h). Peça um trecho ou um vídeo com legendas."
        )}
    with tempfile.TemporaryDirectory(prefix="aw-transcribe-") as tmp:
        audio = _download_audio(url, tmp)
        if not audio:
            return {"error": "não consegui baixar o áudio dessa URL"}
        try:
            segments = _segment_audio(audio, tmp)
        except FileNotFoundError:
            return {"error": "ffmpeg indisponível no servidor (necessário p/ transcrever a fala)"}
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return {"error": f"falha ao processar o áudio: {str(exc)[:150]}"}
        if not segments:
            return {"error": "não consegui extrair áudio dessa URL"}
        pieces: list[str] = []
        for i, seg in enumerate(segments):
            data = _read_capped(seg)
            if data is None:
                continue
            txt = await _transcribe_bytes(stt_candidates, f"part{i:03d}.mp3", data)
            if txt is None:
                return {"error": (
                    "nenhum servidor de transcrição respondeu (a conexão de Voz Local pode "
                    "não fazer STT e/ou não há chave do provedor de voz)"
                )}
            if txt.strip():
                pieces.append(txt.strip())
        text = " ".join(pieces).strip()
        if not text:
            return {"error": "a transcrição da fala veio vazia (áudio sem voz?)"}
        return {"ok": True, "source": "speech", "text": text}


def _read_capped(path: str) -> bytes | None:
    """Lê a fatia respeitando `_STT_LIMIT` (fatias já são pequenas; guarda contra o
    caso raro de uma faixa não fatiável). None se estourar."""
    try:
        if os.path.getsize(path) > _STT_LIMIT:
            logger.warning("Fatia de áudio %s acima do teto do STT; pulando", path)
            return None
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


async def _transcribe_bytes(cands: list[dict], filename: str, data: bytes) -> str | None:
    """Tenta cada candidato de STT em ordem; None se nenhum aceitar (todos 404/fora)."""
    for cand in cands:
        txt = await _stt_one(cand, filename, data)
        if txt is not None:
            return txt
    return None


def _metadata(info: dict[str, Any]) -> dict[str, Any]:
    """Metadados REAIS da plataforma entregues ao modelo.

    `channel` existe porque a ausência dele causou alucinação de verdade: sem o nome
    do canal no retorno, o modelo inventou um ("Ciência Todo Dia"), foi questionado e
    inventou outro ("Infinity") alegando tê-lo lido na transcrição. O canal não está
    no texto falado — ou vem daqui, ou é chute."""
    out: dict[str, Any] = {}
    title = (info.get("title") or "").strip()
    channel = (info.get("channel") or info.get("uploader") or "").strip()
    if title:
        out["title"] = title
    if channel:
        out["channel"] = channel
    if info.get("duration"):
        out["duration"] = int(info["duration"])
    up = (info.get("upload_date") or "").strip()
    if len(up) == 8 and up.isdigit():  # YYYYMMDD -> ISO
        out["upload_date"] = f"{up[:4]}-{up[4:6]}-{up[6:]}"
    if info.get("webpage_url"):
        out["webpage_url"] = info["webpage_url"]
    return out


def _cache_get(key: tuple[str, str]) -> dict[str, Any] | None:
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, payload = hit
    if time.time() - ts > _CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return payload


def _cache_put(key: tuple[str, str], payload: dict[str, Any]) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
        _CACHE.pop(oldest, None)
    _CACHE[key] = (time.time(), payload)


async def transcribe_url(
    url: str, lang: str, max_chars: int, stt_candidates: list[dict],
) -> dict[str, Any]:
    """Ponto de entrada: legendas primeiro, fala como fallback.

    O transcript COMPLETO é cacheado por (url, lang); só o recorte depende de
    `max_chars`, então re-chamadas para "ver mais" não re-baixam nada."""
    key = (url, (lang or "").strip())
    cached = _cache_get(key)
    if cached is not None:
        base = {k: v for k, v in cached.items() if k != "_full_text"}
        return {**base, **_clip(cached["_full_text"], max_chars)}

    try:
        info = _extract_info(url)
    except Exception as exc:  # noqa: BLE001 - yt_dlp.DownloadError e afins
        msg = str(exc)
        if "Unsupported URL" in msg or "not a valid URL" in msg:
            return {"error": "essa URL não é um vídeo/áudio reconhecido"}
        return {"error": f"não consegui abrir a mídia: {msg[:180]}"}
    if not info:
        return {"error": "não consegui abrir a mídia nessa URL"}

    meta = _metadata(info)
    cap = await _via_captions(info, lang)
    if cap is not None:
        text, code = cap
        base = {"ok": True, "source": "captions", **meta, "lang": code}
    else:
        result = await _via_speech(url, info, stt_candidates)
        if "error" in result:
            return result
        text = result["text"]
        base = {"ok": True, "source": "speech", **meta, "lang": lang or "auto"}

    _cache_put(key, {**base, "_full_text": text})
    return {**base, **_clip(text, max_chars)}
