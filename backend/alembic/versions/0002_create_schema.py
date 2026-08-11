"""create core schema

Creates the SPEC data model — notebooks, sources, chunks, chat_messages,
llm_calls — plus the gateway's llm_cache table. Enables the pgvector extension
and builds an HNSW cosine index on chunks.embedding.

Revision ID: 0002_create_schema
Revises: 0001_initial
Create Date: 2026-08-08

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_create_schema"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 384

# Native Postgres ENUM types (create_type=False -> we create/drop them
# explicitly so re-running against a fresh DB is deterministic).
source_type = postgresql.ENUM(
    "pdf", "docx", "url", name="source_type", create_type=False
)
source_status = postgresql.ENUM(
    "pending", "processing", "ready", "failed", name="source_status", create_type=False
)
message_role = postgresql.ENUM(
    "user", "assistant", name="message_role", create_type=False
)
llm_call_status = postgresql.ENUM(
    "ok", "fallback", "error", name="llm_call_status", create_type=False
)

_ENUMS = (source_type, source_status, message_role, llm_call_status)


def upgrade() -> None:
    bind = op.get_bind()

    # pgvector extension (docker-compose enables it too; idempotent here so the
    # migration is self-contained against any Postgres).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    for enum_type in _ENUMS:
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "notebooks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("notebook_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", source_type, nullable=False),
        sa.Column("original_name_or_url", sa.Text(), nullable=False),
        sa.Column(
            "ingested_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "status",
            source_status,
            server_default="pending",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["notebook_id"], ["notebooks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sources_notebook_id", "sources", ["notebook_id"])

    op.create_table(
        "chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("notebook_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("char_span", postgresql.INT4RANGE(), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["notebook_id"], ["notebooks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chunks_source_id", "chunks", ["source_id"])
    op.create_index("ix_chunks_notebook_id", "chunks", ["notebook_id"])
    # HNSW index for approximate nearest-neighbour cosine search. Matches the
    # SPEC's cosine similarity retrieval (vector_cosine_ops).
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "chat_messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("notebook_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", message_role, nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "cited_chunk_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["notebook_id"], ["notebooks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_chat_messages_notebook_id", "chat_messages", ["notebook_id"]
    )

    op.create_table(
        "llm_calls",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("notebook_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("status", llm_call_status, nullable=False),
        sa.Column(
            "cache_hit",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["notebook_id"], ["notebooks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["chat_messages.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_calls_notebook_id", "llm_calls", ["notebook_id"])
    op.create_index("ix_llm_calls_message_id", "llm_calls", ["message_id"])

    op.create_table(
        "llm_cache",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("cache_key", sa.Text(), nullable=False),
        sa.Column("notebook_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["notebook_id"], ["notebooks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cache_key", name="uq_llm_cache_cache_key"),
    )
    op.create_index("ix_llm_cache_notebook_id", "llm_cache", ["notebook_id"])


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index("ix_llm_cache_notebook_id", table_name="llm_cache")
    op.drop_table("llm_cache")

    op.drop_index("ix_llm_calls_message_id", table_name="llm_calls")
    op.drop_index("ix_llm_calls_notebook_id", table_name="llm_calls")
    op.drop_table("llm_calls")

    op.drop_index("ix_chat_messages_notebook_id", table_name="chat_messages")
    op.drop_table("chat_messages")

    op.drop_index("ix_chunks_embedding_hnsw", table_name="chunks")
    op.drop_index("ix_chunks_notebook_id", table_name="chunks")
    op.drop_index("ix_chunks_source_id", table_name="chunks")
    op.drop_table("chunks")

    op.drop_index("ix_sources_notebook_id", table_name="sources")
    op.drop_table("sources")

    op.drop_table("notebooks")

    for enum_type in reversed(_ENUMS):
        enum_type.drop(bind, checkfirst=True)
