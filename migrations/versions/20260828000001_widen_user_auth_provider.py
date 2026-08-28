"""Widen the user authentication provider column.

The email-link provider value is longer than the legacy six-character
database column, which caused Firebase sign-in user sync to fail.

Revision ID: 20260828000001
Revises: 20260827000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828000001"
down_revision: str | None = "20260827000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "provider",
        existing_type=sa.String(length=6),
        type_=sa.String(length=32),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "provider",
        existing_type=sa.String(length=32),
        type_=sa.String(length=6),
        existing_nullable=False,
    )
