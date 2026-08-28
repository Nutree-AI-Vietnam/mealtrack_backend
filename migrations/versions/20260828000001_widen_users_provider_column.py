"""Widen users.provider so EMAIL_LINK and ANONYMOUS values persist.

The original VARCHAR(6) fit GOOGLE/APPLE names only. Syncing an email-link
account writes EMAIL_LINK (10 chars) and truncates.

Revision ID: 20260828000001
Revises: 20260825000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828000001"
down_revision: str | None = "20260825000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "provider",
        existing_type=sa.String(length=6),
        type_=sa.String(length=32),
        existing_nullable=False,
        postgresql_using="provider::text",
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "provider",
        existing_type=sa.String(length=32),
        type_=sa.String(length=6),
        existing_nullable=False,
        postgresql_using="provider::text",
    )
