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
from app.models import Chunk, Source, SourceStatus, SourceType
from app.parsing import ParseError, parse_docx, parse_pdf, parse_url

logger = logging.getLogger("app.ingestion")

# Bounds how many sources parse/fetch/embed at once — see config.py's
# max_concurrent_ingestions for why this exists.
_ingestion_semaphore = threading.Semaphore(get_settings().max_concurrent_ingestions)


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
        db.commit()

        # 1. Parse -> plain text
        if source_type == SourceType.pdf:
            assert file_bytes is not None
            text = parse_pdf(file_bytes)
        elif source_type == SourceType.docx:
            assert file_bytes is not None
            text = parse_docx(file_bytes)
        elif source_type == SourceType.url:
            assert url is not None
            text = parse_url(url)
        else:  # pragma: no cover - guarded by the router
            raise ParseError(f"Unsupported source type: {source_type}")

        # 2. Chunk (~500 tokens, ~50 overlap; word-count approximation)
        pieces = chunk_text(text)
        if not pieces:
            raise ParseError("No chunks produced from source content")

        # 3. Embed locally (batch)
        vectors = embeddings.embed_texts([p.content for p in pieces])

        # 4. Store, scoped to notebook_id + source_id
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
                db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("ingest_source: could not mark source %s failed", source_id)
    finally:
        db.close()
