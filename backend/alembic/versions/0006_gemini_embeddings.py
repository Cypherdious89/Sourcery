"""switch embeddings from local MiniLM (384-dim) to hosted gemini-embedding-001 (768-dim)

pgvector's vector(N) type carries a fixed dimension, and an old 384-dim
vector cannot be reinterpreted as a 768-dim one, so existing embeddings are
unrecoverable — this deletes them rather than leaving stale, wrongly-typed
data behind. Any notebook with sources ingested before this migration needs
those sources re-added to be searchable again.

llm_cache's exact-match rows (looked up by cache_key, independent of
embeddings) are left alone; only query_embedding is cleared, since the
semantic (paraphrase) cache path is now disabled by default anyway pending
a proper threshold measurement for the new embedding space (see
app/config.py's semantic_cache_enabled).

Revision ID: 0006_gemini_embeddings
Revises: 0005_rate_limit_index
Create Date: 2026-08-23

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0006_gemini_embeddings"
down_revision: Union[str, None] = "0005_rate_limit_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_DIM = 384
NEW_DIM = 768


def upgrade() -> None:
    # chunks.embedding — delete rows first: they'd otherwise be stuck with a
    # 384-dim value in a column about to be declared vector(768).
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.execute("DELETE FROM chunks")
    op.alter_column(
        "chunks", "embedding", type_=Vector(NEW_DIM), postgresql_using="NULL"
    )
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    # llm_cache.query_embedding — nullable, so just clear it rather than
    # deleting the (still-valid, embedding-independent) exact-cache rows.
    op.execute("DROP INDEX IF EXISTS ix_llm_cache_query_embedding_hnsw")
    op.execute("UPDATE llm_cache SET query_embedding = NULL")
    op.alter_column(
        "llm_cache", "query_embedding", type_=Vector(NEW_DIM), postgresql_using="NULL"
    )
    op.execute(
        "CREATE INDEX ix_llm_cache_query_embedding_hnsw ON llm_cache "
        "USING hnsw (query_embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.execute("DELETE FROM chunks")
    op.alter_column(
        "chunks", "embedding", type_=Vector(OLD_DIM), postgresql_using="NULL"
    )
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    op.execute("DROP INDEX IF EXISTS ix_llm_cache_query_embedding_hnsw")
    op.execute("UPDATE llm_cache SET query_embedding = NULL")
    op.alter_column(
        "llm_cache", "query_embedding", type_=Vector(OLD_DIM), postgresql_using="NULL"
    )
    op.execute(
        "CREATE INDEX ix_llm_cache_query_embedding_hnsw ON llm_cache "
        "USING hnsw (query_embedding vector_cosine_ops)"
    )
