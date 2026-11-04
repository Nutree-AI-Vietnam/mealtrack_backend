"""add user avatar fields

Revision ID: 20261026102822211100
Revises: 20261026102517824023
Create Date: 2026-10-26 17:28:22.252698

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261026102822211100"
down_revision: str | None = "20261026102517824023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "avatar_kind",
            sa.String(length=16),
            server_default="auto",
            nullable=False,
        ),
    )
    op.add_column(
        "users", sa.Column("avatar_image_id", sa.String(length=64), nullable=True)
    )
    op.add_column("users", sa.Column("avatar_image_url", sa.Text(), nullable=True))
    op.add_column(
        "users", sa.Column("avatar_character", sa.String(length=32), nullable=True)
    )
    op.add_column("users", sa.Column("avatar_color", sa.SmallInteger(), nullable=True))
    op.create_check_constraint(
        "check_users_avatar_kind",
        "users",
        "avatar_kind IN ('auto', 'photo', 'character', 'initials')",
    )
    op.create_check_constraint(
        "check_users_avatar_color",
        "users",
        "avatar_color IS NULL OR (avatar_color >= 0 AND avatar_color < 8)",
    )


def downgrade() -> None:
    op.drop_constraint("check_users_avatar_color", "users", type_="check")
    op.drop_constraint("check_users_avatar_kind", "users", type_="check")
    op.drop_column("users", "avatar_color")
    op.drop_column("users", "avatar_character")
    op.drop_column("users", "avatar_image_url")
    op.drop_column("users", "avatar_image_id")
    op.drop_column("users", "avatar_kind")
