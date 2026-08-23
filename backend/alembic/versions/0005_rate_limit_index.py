"""add composite index for rate-limit usage queries

app/rate_limits.py checks each provider/model candidate's usage in the last
60 seconds and since UTC midnight before the gateway attempts it — up to
five such lookups per chat request. Without this index each one is a
sequential scan of llm_calls.

Revision ID: 0005_rate_limit_index
Revises: 0004_semantic_cache
Create Date: 2026-08-23

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0005_rate_limit_index"
down_revision: Union[str, None] = "0004_semantic_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_llm_calls_provider_model_created_at",
        "llm_calls",
        ["provider", "model", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_llm_calls_provider_model_created_at", table_name="llm_calls")
