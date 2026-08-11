"""add users and scope notebooks to an owner

Introduces per-user isolation: a ``users`` table keyed on Google's stable
``sub`` claim, and a required ``notebooks.user_id``.

Existing notebooks predate auth, so they are backfilled to the sentinel
``local-dev`` user — the same identity the API uses when GOOGLE_CLIENT_ID is
unset. That keeps local demo data reachable instead of orphaning it, and lets
``user_id`` be NOT NULL.

Revision ID: 0003_add_users
Revises: 0002_create_schema
Create Date: 2026-08-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_add_users"
down_revision: Union[str, None] = "0002_create_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Fixed so the migration and app.auth agree on the sentinel owner.
LOCAL_DEV_USER_ID = "00000000-0000-0000-0000-0000000000de"
LOCAL_DEV_SUB = "local-dev"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("google_sub", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("picture", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("google_sub", name="uq_users_google_sub"),
    )

    # Sentinel owner for pre-auth data and for auth-disabled local dev.
    op.execute(
        sa.text(
            # Explicit casts: the driver would otherwise bind these as VARCHAR.
            "INSERT INTO users (id, google_sub, email, name) "
            "VALUES (CAST(:id AS uuid), :sub, NULL, 'Local Dev') "
            "ON CONFLICT (google_sub) DO NOTHING"
        ).bindparams(id=LOCAL_DEV_USER_ID, sub=LOCAL_DEV_SUB)
    )

    # Add nullable, backfill, then tighten to NOT NULL so the migration is
    # safe against a database that already has notebooks.
    op.add_column(
        "notebooks",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE notebooks SET user_id = CAST(:id AS uuid) WHERE user_id IS NULL"
        ).bindparams(id=LOCAL_DEV_USER_ID)
    )
    op.alter_column("notebooks", "user_id", nullable=False)
    op.create_foreign_key(
        "fk_notebooks_user_id",
        "notebooks",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_notebooks_user_id", "notebooks", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_notebooks_user_id", table_name="notebooks")
    op.drop_constraint("fk_notebooks_user_id", "notebooks", type_="foreignkey")
    op.drop_column("notebooks", "user_id")
    op.drop_table("users")
