"""add progress percentage to sources

Ingestion now makes real network calls (Gemini embeddings), so its stages
have meaningful, trackable durations — a coarse checkpoint-based percentage
(parsed/chunked/embedding/stored) gives the frontend something better than a
generic spinner, and distinguishes "queued behind MAX_CONCURRENT_INGESTIONS"
(status=pending, progress=0, untouched) from "actively working" (status=
processing, progress climbing).

Revision ID: 0007_source_progress
Revises: 0006_gemini_embeddings
Create Date: 2026-08-23

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_source_progress"
down_revision: Union[str, None] = "0006_gemini_embeddings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("sources", "progress")
