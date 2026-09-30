"""Embeddings das descrições de ferramentas: um modelo só, cache POR TEXTO em disco.

O índice da SIFT guardava os vetores num arquivo por usuário cuja chave é o hash de
TODAS as descrições juntas: mudar UMA (atualização do app, tool nova do usuário)
invalidava tudo e re-embutia as ~80 descrições — ~75 s na primeira mensagem, travando
o turno (a Observabilidade mostrou: `setup:tools`). E cada usuário carregava a sua
própria cópia do modelo ONNX.

Aqui: um `FastEmbedder` compartilhado (carregado uma vez), e cada texto vira vetor
UMA vez — guardado por hash em `<sift_index_cache_dir>/texts-<modelo>.npz`, valendo
para todos os usuários e sobrevivendo a restarts. Só o que é novo/mudou é calculado.
O texto do VETOR é cortado em `_MAX_CHARS` (o começo da descrição diz o que a tool faz;
o custo do modelo cresce com o tamanho). A busca por palavras (BM25) continua usando o
texto inteiro — isso é da SIFT, não passa por aqui.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from typing import Any, Sequence

logger = logging.getLogger(__name__)

_MAX_CHARS = 400
_MODEL = os.getenv("SIFT_EMBED_MODEL", "BAAI/bge-small-en-v1.5")

_lock = threading.Lock()
_shared: "CachedEmbedder | None" = None


def _key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class CachedEmbedder:
    """Mesma interface do `sift.embeddings.FastEmbedder` (embed / embed_query)."""

    def __init__(self, cache_dir: str | None, max_chars: int = _MAX_CHARS) -> None:
        self.max_chars = max_chars
        # entra na chave do cache de índice da SIFT: mudar o corte invalida uma vez
        self.model_name = f"{_MODEL}|cut{max_chars}"
        self._inner: Any = None
        self._vecs: dict[str, Any] = {}
        self._path = None
        if cache_dir:
            try:
                os.makedirs(cache_dir, exist_ok=True)
                slug = _MODEL.replace("/", "_")
                self._path = os.path.join(cache_dir, f"texts-{slug}-{max_chars}.npz")
            except OSError as exc:
                logger.warning("cache de embeddings indisponível (%s)", exc)
        self._load()

    # -- modelo ---------------------------------------------------------------
    def _model(self) -> Any:
        if self._inner is None:
            with _lock:
                if self._inner is None:
                    from sift.embeddings import FastEmbedder

                    self._inner = FastEmbedder(_MODEL)
        return self._inner

    # -- disco ----------------------------------------------------------------
    def _load(self) -> None:
        if not self._path or not os.path.exists(self._path):
            return
        try:
            import numpy as np

            with open(self._path, "rb") as fh:
                data = np.load(fh, allow_pickle=False)
                keys, vecs = list(data["keys"]), data["vectors"]
            self._vecs = {str(k): vecs[i] for i, k in enumerate(keys)}
        except Exception as exc:  # noqa: BLE001 - cache corrompido: recalcula
            logger.warning("cache de embeddings ilegível (%s); recalculando o que precisar", exc)
            self._vecs = {}

    def _save(self) -> None:
        if not self._path or not self._vecs:
            return
        try:
            import numpy as np

            keys = list(self._vecs)
            tmp = f"{self._path}.tmp"
            with open(tmp, "wb") as fh:
                np.savez(fh, keys=np.array(keys), vectors=np.stack([self._vecs[k] for k in keys]))
            os.replace(tmp, self._path)
        except Exception as exc:  # noqa: BLE001 - só perde o atalho do próximo boot
            logger.warning("não gravei o cache de embeddings (%s)", exc)

    # -- interface --------------------------------------------------------------
    def embed(self, texts: Sequence[str]) -> list[Any]:
        cortes = [t[: self.max_chars] for t in texts]
        chaves = [_key(t) for t in cortes]
        with _lock:
            faltam = {k: t for k, t in zip(chaves, cortes) if k not in self._vecs}
        if faltam:
            novos = self._model().embed(list(faltam.values()))
            with _lock:
                for k, v in zip(faltam, novos):
                    self._vecs[k] = v
                self._save()
            logger.info("embeddings: %d novo(s), %d do cache", len(faltam), len(chaves) - len(faltam))
        return [self._vecs[k] for k in chaves]

    def embed_query(self, texts: Sequence[str]) -> list[Any]:
        m = self._model()
        fn = getattr(m, "embed_query", None) or m.embed
        return fn(list(texts))


def shared(cache_dir: str | None) -> CachedEmbedder:
    """O embedder do processo (um modelo ONNX para todos os usuários)."""
    global _shared
    if _shared is None:
        with _lock:
            if _shared is None:
                _shared = CachedEmbedder(cache_dir)
    return _shared
