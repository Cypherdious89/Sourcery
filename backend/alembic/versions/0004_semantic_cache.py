"""add semantic-cache columns to llm_cache

Stores the raw user question, its embedding, and a fingerprint of the
surrounding context (retrieved chunks + conversation history) so the gateway
can serve a paraphrased question from cache.

All columns are nullable: rows written before this migration stay valid and
simply never match semantically.

Revision ID: 0004_semantic_cache
Revises: 0003_add_users
Create Date: 2026-08-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0004_semantic_cache"
down_revision: Union[str, None] = "0003_add_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 384


def upgrade() -> None:
    op.add_column("llm_cache", sa.Column("query_text", sa.Text(), nullable=True))
    op.add_column(
        "llm_cache", sa.Column("query_embedding", Vector(EMBEDDING_DIM), nullable=True)
    )
    op.add_column("llm_cache", sa.Column("context_hash", sa.Text(), nullable=True))

    # Same index strategy as chunks.embedding — cosine distance via HNSW.
    op.execute(
        "CREATE INDEX ix_llm_cache_query_embedding_hnsw ON llm_cache "
        "USING hnsw (query_embedding vector_cosine_ops)"
    )
    # Semantic lookups always filter by notebook and context first.
    op.create_index(
        "ix_llm_cache_notebook_context", "llm_cache", ["notebook_id", "context_hash"]
    )


def downgrade() -> None:
    op.drop_index("ix_llm_cache_notebook_context", table_name="llm_cache")
    op.execute("DROP INDEX IF EXISTS ix_llm_cache_query_embedding_hnsw")
    op.drop_column("llm_cache", "context_hash")
    op.drop_column("llm_cache", "query_embedding")
    op.drop_column("llm_cache", "query_text")
