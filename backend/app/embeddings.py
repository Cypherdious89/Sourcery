"""Local embedding model.

Loads the sentence-transformers model (``all-MiniLM-L6-v2``, 384-dim) exactly
once and reuses it for every request. The model is warmed at app startup (see
``app.main`` lifespan) so no request pays the load cost.
"""

from __future__ import annotations

import threading

from app.config import get_settings

_settings = get_settings()

# The heavy import (torch + sentence-transformers) is done lazily inside
# get_model() so importing this module stays cheap.
_model = None
_model_lock = threading.Lock()

# Serializes every call into model.encode(). This is not about correctness of
# a single call — it's because concurrent encode() calls on this model
# reproducibly SEGFAULT the whole process on PyTorch's MPS (Apple GPU) backend
# (confirmed: two threads calling embed_text() at once crashes with SIGSEGV,
# every time, immediately). Multiple requests reach this module concurrently
# in normal use — e.g. a chat request's query embedding running in the
# request thread while a background ingestion task embeds chunks, or several
# sources uploaded together each spawning their own ingestion task — so this
# lock is load-bearing, not defensive boilerplate. The cost is small: MiniLM
# inference is milliseconds, and this app's traffic is far below where
# serialization would matter.
_encode_lock = threading.Lock()


def get_model():
    """Return the shared SentenceTransformer instance, loading it on first use."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                import torch
                from sentence_transformers import SentenceTransformer

                # BLAS/torch default to one thread per CPU core, and each
                # thread pre-allocates its own working buffers on first use —
                # on a memory-constrained host (e.g. Render's free 512MB
                # tier) that overhead is pure waste: MiniLM inference is
                # milliseconds regardless, and _encode_lock already
                # serializes every call, so there's no parallelism to lose.
                torch.set_num_threads(1)
                _model = SentenceTransformer(_settings.embedding_model)
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts into normalized 384-dim vectors (for cosine)."""
    if not texts:
        return []
    model = get_model()
    with _encode_lock:
        vectors = model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
    return [v.tolist() for v in vectors]


def embed_text(text: str) -> list[float]:
    """Embed a single text into a normalized 384-dim vector."""
    return embed_texts([text])[0]
