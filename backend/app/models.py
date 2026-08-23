"""SQLAlchemy ORM models mirroring the SPEC "Data Model" section exactly.

Tables: notebooks, sources, chunks, chat_messages, llm_calls, plus an
llm_cache table backing the gateway's response cache.

Schema is created/managed via Alembic migrations (see alembic/versions),
not via ``Base.metadata.create_all`` — these models are the ORM mapping and
the source of truth Alembic autogenerate compares against.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import ARRAY, INT4RANGE, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# --- Embedding dimension: gemini-embedding-001, truncated to 768. Keep in
# sync with app/config.py's embedding_dim and the vector column type in
# alembic/versions/0006_gemini_embeddings.py. ---
EMBEDDING_DIM = 768


# --------------------------------------------------------------------------- #
# Enums (native Postgres ENUM types)
# --------------------------------------------------------------------------- #
class SourceType(str, enum.Enum):
    pdf = "pdf"
    docx = "docx"
    url = "url"


class SourceStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class MessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"


class LLMCallStatus(str, enum.Enum):
    ok = "ok"
    fallback = "fallback"
    error = "error"


# Shared column-type helpers so the ORM references the SAME pg type names the
# migration creates (values_callable => store the enum *values*, e.g. "pdf").
def _pg_enum(python_enum: type[enum.Enum], name: str) -> SAEnum:
    return SAEnum(
        python_enum,
        name=name,
        native_enum=True,
        values_callable=lambda e: [member.value for member in e],
    )


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class User(Base):
    """A signed-in Google account.

    ``google_sub`` is Google's stable subject claim — the only identifier safe
    to key on, since email addresses can change. The sentinel sub
    ``local-dev`` owns everything created while auth is disabled (no
    GOOGLE_CLIENT_ID configured).
    """

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("google_sub", name="uq_users_google_sub"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    google_sub: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    picture: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()

    notebooks: Mapped[list[Notebook]] = relationship(back_populates="user")


class Notebook(Base):
    __tablename__ = "notebooks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()

    user: Mapped[User] = relationship(back_populates="notebooks")

    sources: Mapped[list[Source]] = relationship(
        back_populates="notebook", cascade="all, delete-orphan"
    )
    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="notebook", cascade="all, delete-orphan"
    )
    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="notebook", cascade="all, delete-orphan"
    )


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = _uuid_pk()
    notebook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notebooks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type: Mapped[SourceType] = mapped_column(
        _pg_enum(SourceType, "source_type"), nullable=False
    )
    original_name_or_url: Mapped[str] = mapped_column(Text, nullable=False)
    ingested_at: Mapped[datetime] = _created_at()
    status: Mapped[SourceStatus] = mapped_column(
        _pg_enum(SourceStatus, "source_status"),
        nullable=False,
        server_default=SourceStatus.pending.value,
    )
    # Coarse checkpoint progress (0-100) through parse -> chunk -> embed ->
    # store, updated as ingestion.py advances. 0 while queued (pending) or
    # untouched; only climbs once processing actually starts.
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    notebook: Mapped[Notebook] = relationship(back_populates="sources")
    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        # HNSW cosine ANN index — see migration 0002 for rationale.
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    notebook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notebooks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # int4range covering the [start, end) character offsets within the source.
    char_span: Mapped[object | None] = mapped_column(INT4RANGE, nullable=True)

    source: Mapped[Source] = relationship(back_populates="chunks")
    notebook: Mapped[Notebook] = relationship(back_populates="chunks")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    notebook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notebooks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[MessageRole] = mapped_column(
        _pg_enum(MessageRole, "message_role"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Chunk ids the assistant cited (empty/NULL for user messages).
    cited_chunk_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True
    )
    created_at: Mapped[datetime] = _created_at()

    notebook: Mapped[Notebook] = relationship(back_populates="messages")


class LLMCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = _uuid_pk()
    notebook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notebooks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Nullable: some gateway calls aren't tied to a persisted chat message.
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[LLMCallStatus] = mapped_column(
        _pg_enum(LLMCallStatus, "llm_call_status"), nullable=False
    )
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    created_at: Mapped[datetime] = _created_at()


class LLMCache(Base):
    """Gateway response cache.

    ``cache_key`` is a hash of ``(notebook_id, normalized_prompt)``; on a hit
    the gateway returns ``response_text`` plus the stored usage metadata with
    ``cache_hit=True``. ``created_at`` supports a simple TTL / inspection.
    """

    __tablename__ = "llm_cache"
    __table_args__ = (
        UniqueConstraint("cache_key", name="uq_llm_cache_cache_key"),
        Index(
            "ix_llm_cache_query_embedding_hnsw",
            "query_embedding",
            postgresql_using="hnsw",
            postgresql_ops={"query_embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    cache_key: Mapped[str] = mapped_column(Text, nullable=False)
    # --- Semantic cache ---
    # The raw user question and its embedding, so a paraphrase ("what's RAG?"
    # vs "what is RAG?") can hit the same entry. The full prompt is NOT
    # embedded: it is mostly fixed instructions plus retrieved chunks, and
    # MiniLM truncates at 256 tokens, so every prompt would look identical.
    query_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    query_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )
    # Fingerprint of the retrieved chunks + conversation history. A semantic
    # hit is only valid when the surrounding context is identical, otherwise
    # adding a source would keep returning the stale answer.
    context_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    notebook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notebooks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Metadata about the cached response, replayed on a cache hit.
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    created_at: Mapped[datetime] = _created_at()
