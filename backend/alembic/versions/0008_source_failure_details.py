"""add safe ingestion failure details to sources

Raw ingestion exceptions stay in backend logs. These nullable fields carry a
stable code and short safe message for the source-status UI.

Revision ID: 0008_source_failure_details
Revises: 0007_source_progress
Create Date: 2026-08-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_source_failure_details"
down_revision: Union[str, None] = "0007_source_progress"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("error_code", sa.Text(), nullable=True))
    op.add_column("sources", sa.Column("error_message", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "error_message")
    op.drop_column("sources", "error_code")
