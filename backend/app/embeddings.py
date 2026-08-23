"""Hosted embedding model (Gemini).

Was local (sentence-transformers/MiniLM) until that stack's baseline memory
footprint — torch plus the loaded model plus per-document parse buffers —
turned out to exceed Render's free-tier 512MB ceiling even for a single
typical source, confirmed in production (SIGKILL/exit 137, "Ran out of
memory") on a plain 19-page PDF with no concurrency involved. Switching to a
hosted embedding API removes that baseline entirely at the cost of a network
dependency and this account's quota (gemini-embedding-001 free tier: 100
RPM / 30,000 TPM / 1,000 RPD — measured from aistudio.google.com/rate-limit).

Uses the same GEMINI_API_KEY as the chat providers — no separate signup.
``task_type`` is set asymmetrically per Google's guidance for retrieval:
documents (chunks, embedded once at ingestion) use RETRIEVAL_DOCUMENT, and
queries (embedded per chat turn / semantic-cache lookup) use RETRIEVAL_QUERY.
This maps 1:1 onto the existing embed_texts (documents) / embed_text (query)
split, so no call site needed to change.
"""

from __future__ import annotations

from collections.abc import Callable
from math import ceil

from app.config import get_settings

_settings = get_settings()

# BatchEmbedContentsRequest caps at 100 texts per call (confirmed against the
# live API — larger batches fail with a 400 INVALID_ARGUMENT).
_MAX_BATCH = 100


def _client():
    from google import genai

    return genai.Client(api_key=_settings.gemini_api_key)


def _embed(
    texts: list[str],
    *,
    task_type: str,
    on_batch: Callable[[int, int], None] | None = None,
) -> list[list[float]]:
    if not texts:
        return []
    from google.genai import types

    client = _client()
    config = types.EmbedContentConfig(
        output_dimensionality=_settings.embedding_dim, task_type=task_type
    )
    total_batches = ceil(len(texts) / _MAX_BATCH)
    vectors: list[list[float]] = []
    for batch_num, i in enumerate(range(0, len(texts), _MAX_BATCH), start=1):
        batch = texts[i : i + _MAX_BATCH]
        resp = client.models.embed_content(
            model=_settings.embedding_model, contents=batch, config=config
        )
        vectors.extend(e.values for e in resp.embeddings)
        if on_batch is not None:
            on_batch(batch_num, total_batches)
    return vectors


def embed_texts(
    texts: list[str], *, on_batch: Callable[[int, int], None] | None = None
) -> list[list[float]]:
    """Embed a batch of document/chunk texts (ingestion path).

    ``on_batch(batches_done, total_batches)`` fires after each ≤100-text
    batch completes — lets ingestion.py report real progress mid-embed for
    the rare source large enough to need more than one batch, rather than
    jumping straight from "embedding" to "done".
    """
    return _embed(texts, task_type="RETRIEVAL_DOCUMENT", on_batch=on_batch)


def embed_text(text: str) -> list[float]:
    """Embed a single query text (chat retrieval / semantic-cache lookup)."""
    return _embed([text], task_type="RETRIEVAL_QUERY")[0]
