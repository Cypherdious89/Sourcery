"""initial empty migration

Establishes the migration baseline. The actual schema (notebooks, sources,
chunks, chat_messages, llm_calls, and the pgvector extension) is added in a
later phase; this revision intentionally does nothing.

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-08

"""
from typing import Sequence, Union

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
