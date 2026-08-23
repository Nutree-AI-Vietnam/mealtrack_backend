"""Persist FatSecret serving text and Vietnamese serving labels.

Revision ID: 20260823000001
Revises: 20260820000002
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260823000001"
down_revision: str | None = "20260820000002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "food_reference_serving_sizes",
        sa.Column("description", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "food_reference_serving_sizes",
        sa.Column("name_vi", sa.String(length=100), nullable=True),
    )
    op.create_table(
        "serving_phrase_translation",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_key", sa.String(length=120), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("translated_text", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_key",
            "language",
            name="uq_serving_phrase_translation_source_language",
        ),
    )


def downgrade() -> None:
    op.drop_table("serving_phrase_translation")
    op.drop_column("food_reference_serving_sizes", "name_vi")
    op.drop_column("food_reference_serving_sizes", "description")
