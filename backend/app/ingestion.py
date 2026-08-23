"""Ingestion pipeline: parse -> chunk -> embed -> store.

Runs as a FastAPI BackgroundTask. Opens its own DB session (the request's
session is already closed by the time this runs) and drives the source's
``status`` through processing -> ready, or failed on any error.
"""

from __future__ import annotations

import ctypes
import gc
import logging
import sys
import threading
import uuid

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import Range

from app import embeddings
from app.chunking import chunk_text
from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    UNTITLED_NOTEBOOK_TITLE,
    Chunk,
    Notebook,
    Source,
    SourceStatus,
    SourceType,
)
from app.parsing import ParseError, parse_docx, parse_pdf, parse_url

# Keeps an auto-derived notebook title from becoming unreasonably long — a
# verbose page <title> or a long filename shouldn't blow out the UI.
_MAX_AUTO_TITLE_LEN = 200

logger = logging.getLogger("app.ingestion")

# Bounds how many sources parse/fetch/embed at once — see config.py's
# max_concurrent_ingestions for why this exists.
_ingestion_semaphore = threading.Semaphore(get_settings().max_concurrent_ingestions)

# Coarse checkpoint progress (0-100). Embedding is now a real network call
# (Gemini), not an instant local one, so it gets a range rather than a single
# jump — see embeddings.embed_texts' on_batch for how PROGRESS_EMBED_START..
# PROGRESS_EMBED_DONE gets subdivided for a source large enough to need more
# than one embedding batch (rare — most sources fit in one).
PROGRESS_STARTED = 10
PROGRESS_PARSED = 30
PROGRESS_CHUNKED = 50
PROGRESS_EMBED_START = 50
PROGRESS_EMBED_DONE = 90
PROGRESS_READY = 100


def classify_ingestion_failure(
    exc: Exception, *, stage: str | None = None
) -> tuple[str, str]:
    """Map an internal ingestion exception to a safe UI-facing payload.

    Exception messages can include URLs, file paths, or provider internals, so
    they remain in server logs only. The browser gets a stable error code plus
    a short recovery-oriented message.
    """
    detail = str(exc).lower()
    if "no extractable text" in detail or "no main content extracted" in detail:
        return "EMPTY_CONTENT", "No extractable text was found in this source."
    if "fetch url" in detail or "scrape url" in detail:
        return (
            "URL_FETCH_FAILED",
            "The URL could not be fetched. Check that it is public and try again.",
        )
    if isinstance(exc, ParseError):
        return "PARSE_FAILED", "This source could not be parsed."
    if stage == "embedding":
        return (
            "EMBEDDING_FAILED",
            "Embeddings could not be created right now. Please retry.",
        )
    return "INGESTION_FAILED", "The source could not be processed. Please try again."


def ingest_source(
    source_id: uuid.UUID,
    notebook_id: uuid.UUID,
    source_type: SourceType,
    *,
    file_bytes: bytes | None = None,
    url: str | None = None,
) -> None:
    """Parse, chunk, embed, and store a source's content.

    Idempotent-ish: any pre-existing chunks for this source are cleared before
    re-inserting, so a retry doesn't duplicate rows. Blocks until a slot under
    max_concurrent_ingestions is free, so a burst of sources added together
    queues rather than running all at once.
    """
    with _ingestion_semaphore:
        try:
            _ingest_source(source_id, notebook_id, source_type, file_bytes=file_bytes, url=url)
        finally:
            _release_memory_to_os()


def _derive_title(
    source_type: SourceType, original_name_or_url: str, url_title: str | None
) -> str:
    """Best-effort notebook title from a source, once it's ready.

    PDFs/DOCXs use their filename (minus extension) — there's nothing richer
    to go on. URLs prefer the page's own <title>, falling back to the raw URL
    if trafilatura couldn't find one.
    """
    if source_type == SourceType.url:
        title = (url_title or original_name_or_url).strip()
    else:
        title = original_name_or_url.rsplit(".", 1)[0].strip() or original_name_or_url
    return title[:_MAX_AUTO_TITLE_LEN]


def _release_memory_to_os() -> None:
    """Hand freed heap memory back to the OS after a source finishes.

    glibc's malloc doesn't return freed arenas to the OS on its own — it
    keeps them for reuse, so RSS only ever grows across a long-running
    process. On a memory-constrained host (Render's free 512MB tier) that
    means a burst of large sources can ratchet RSS up permanently even
    though nothing is actually leaked. malloc_trim(0) forces the return;
    it's a glibc-only call, so this is a no-op everywhere else (e.g. local
    macOS dev, which uses a different allocator).
    """
    gc.collect()
    if sys.platform == "linux":
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except OSError:
            pass


def _ingest_source(
    source_id: uuid.UUID,
    notebook_id: uuid.UUID,
    source_type: SourceType,
    *,
    file_bytes: bytes | None = None,
    url: str | None = None,
) -> None:
    db = SessionLocal()
    try:
        source = db.get(Source, source_id)
        if source is None:
            logger.error("ingest_source: source %s not found", source_id)
            return

        source.status = SourceStatus.processing
        source.progress = PROGRESS_STARTED
        source.error_code = None
        source.error_message = None
        db.commit()

        # 1. Parse -> plain text
        stage = "parsing"
        url_title: str | None = None
        if source_type == SourceType.pdf:
            assert file_bytes is not None
            text = parse_pdf(file_bytes)
        elif source_type == SourceType.docx:
            assert file_bytes is not None
            text = parse_docx(file_bytes)
        elif source_type == SourceType.url:
            assert url is not None
            parsed_url = parse_url(url)
            text, url_title = parsed_url.text, parsed_url.title
        else:  # pragma: no cover - guarded by the router
            raise ParseError(f"Unsupported source type: {source_type}")

        source.progress = PROGRESS_PARSED
        db.commit()

        # 2. Chunk (~500 tokens, ~50 overlap; word-count approximation)
        stage = "chunking"
        pieces = chunk_text(text)
        if not pieces:
            raise ParseError("No chunks produced from source content")

        source.progress = PROGRESS_CHUNKED
        db.commit()

        # 3. Embed (Gemini, hosted — see app/embeddings.py)
        stage = "embedding"
        def _on_batch(done: int, total: int) -> None:
            span = PROGRESS_EMBED_DONE - PROGRESS_EMBED_START
            source.progress = PROGRESS_EMBED_START + int(span * done / total)
            db.commit()

        vectors = embeddings.embed_texts(
            [p.content for p in pieces], on_batch=_on_batch
        )
        source.progress = PROGRESS_EMBED_DONE
        db.commit()

        # 4. Store, scoped to notebook_id + source_id
        stage = "storing"
        db.execute(delete(Chunk).where(Chunk.source_id == source_id))
        db.add_all(
            [
                Chunk(
                    source_id=source_id,
                    notebook_id=notebook_id,
                    content=piece.content,
                    embedding=vector,
                    chunk_index=piece.chunk_index,
                    char_span=Range(piece.start_char, piece.end_char),
                )
                for piece, vector in zip(pieces, vectors)
            ]
        )

        source.status = SourceStatus.ready
        source.progress = PROGRESS_READY

        # Auto-name a still-untitled notebook from its first source to reach
        # ready — only fires once, since the title stops matching the
        # sentinel after this.
        notebook = db.get(Notebook, notebook_id)
        if notebook is not None and notebook.title == UNTITLED_NOTEBOOK_TITLE:
            notebook.title = _derive_title(
                source_type, source.original_name_or_url, url_title
            )

        db.commit()
        logger.info(
            "ingest_source: source %s ready (%d chunks)", source_id, len(pieces)
        )
    except Exception as exc:  # noqa: BLE001 - any failure marks the source failed
        db.rollback()
        logger.exception("ingest_source: failed for source %s: %s", source_id, exc)
        try:
            source = db.get(Source, source_id)
            if source is not None:
                source.status = SourceStatus.failed
                source.error_code, source.error_message = classify_ingestion_failure(
                    exc, stage=stage
                )
                db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("ingest_source: could not mark source %s failed", source_id)
    finally:
        db.close()
