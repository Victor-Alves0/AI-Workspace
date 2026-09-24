"""Voz EMBUTIDA (TTS) — Kokoro-82M rodando no próprio processo (kokoro-onnx).

Substitui o container do Kokoro-FastAPI (~3,8 GB): o mesmo modelo, pelo onnxruntime
que o app já tem (fastembed). Os pesos (fp16, ~169 MB + ~27 MB de vozes) são baixados
no PRIMEIRO uso para o cache, como os modelos de embedding — nada no instalador.

A língua sai do prefixo da voz (convenção do Kokoro): a=inglês EUA, b=inglês UK,
e=espanhol, f=francês, h=hindi, i=italiano, j=japonês, p=português do Brasil,
z=chinês. Português: pf_dora, pm_alex, pm_santa.

Saída em WAV (24 kHz, mono, 16 bits): o navegador toca direto e não exige encoder
(o ffmpeg existe na imagem Docker, mas não no app desktop).
"""
from __future__ import annotations

import io
import logging
import os
import threading
import wave
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
# fp16 (~169 MB): medido na CPU, ~1,2 s para 4 s de fala — o int8 (88 MB) levava ~7 s
# (as matrizes int8 não são aceleradas no onnxruntime de CPU) e o fp32 (310 MB) empata
MODEL_FILE = "kokoro-v1.0.fp16.onnx"
VOICES_FILE = "voices-v1.0.bin"
DEFAULT_VOICE = "pf_dora"
SAMPLE_RATE = 24000
# id nos seletores: "builtin:pf_dora" — o prefixo roteia para este motor (como "el:"
# para o ElevenLabs); um servidor Kokoro externo usa os mesmos nomes SEM prefixo
PREFIX = "builtin:"

# Vozes do Kokoro v1.0 (fixas): listar NÃO pode baixar ~200 MB de modelo
VOICES = [
    "pf_dora", "pm_alex", "pm_santa",
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nicole",
    "af_nova", "af_river", "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir",
    "am_liam", "am_michael", "am_onyx", "am_puck", "am_santa", "bf_alice", "bf_emma",
    "bf_isabella", "bf_lily", "bm_daniel", "bm_fable", "bm_george", "bm_lewis", "ef_dora",
    "em_alex", "em_santa", "ff_siwis", "hf_alpha", "hf_beta", "hm_omega", "hm_psi", "if_sara",
    "im_nicola", "jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo",
    "zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi", "zm_yunjian", "zm_yunxi",
    "zm_yunxia", "zm_yunyang",
]

_LANG_BY_PREFIX = {
    "a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi",
    "i": "it", "j": "ja", "p": "pt-br", "z": "cmn",
}
_lock = threading.Lock()


def available() -> bool:
    """O kokoro-onnx está instalado? (os pesos podem ainda não ter sido baixados)."""
    try:
        import kokoro_onnx  # noqa: F401
    except Exception:  # noqa: BLE001 - ausente ou lib nativa que não carrega
        return False
    return True


def _cache_dir() -> Path:
    base = os.environ.get("AIW_VOICE_DIR") or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.join(Path.home(), ".cache"), "aiworkspace", "kokoro")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _download(name: str) -> Path:
    """Baixa (uma vez) um arquivo do modelo para o cache. Grava em .part e renomeia:
    uma queda no meio não deixa um arquivo pela metade que pareça pronto."""
    import httpx

    dest = _cache_dir() / name
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    logger.info("voz embutida: baixando %s", name)
    with httpx.stream("GET", f"{_BASE}/{name}", follow_redirects=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    tmp.replace(dest)
    return dest


@lru_cache(maxsize=1)
def _engine():
    from kokoro_onnx import Kokoro

    with _lock:
        return Kokoro(str(_download(MODEL_FILE)), str(_download(VOICES_FILE)))


def voices() -> list[str]:
    """Vozes disponíveis (português primeiro). Não baixa nada."""
    return list(VOICES) if available() else []


def lang_for(voice: str) -> str:
    return _LANG_BY_PREFIX.get((voice or "")[:1].lower(), "en-us")


def synthesize(text: str, voice: str = "", speed: float = 1.0) -> tuple[bytes, str]:
    """(áudio WAV, mime). Bloqueante (CPU) — chamar em threadpool."""
    import numpy as np

    eng = _engine()
    nome = (voice or "").removeprefix(PREFIX) or DEFAULT_VOICE
    if nome not in VOICES:  # ex.: uma voz da OpenAI ("alloy") no caminho automático
        nome = DEFAULT_VOICE
    samples, rate = eng.create(text, voice=nome, speed=float(speed or 1.0), lang=lang_for(nome))
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate or SAMPLE_RATE))
        w.writeframes(pcm)
    return buf.getvalue(), "audio/wav"
