"""Add micros JSON to nutrition, food_item, and hydration_entries.

Revision ID: 20260903000001
Revises: 20260902000001
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260903000001"
down_revision: Union[str, Sequence[str], None] = "20260902000001"

branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("nutrition", sa.Column("micros", sa.JSON(), nullable=True))
    op.add_column("food_item", sa.Column("micros", sa.JSON(), nullable=True))
    op.add_column(
        "hydration_entries", sa.Column("micros", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("hydration_entries", "micros")
    op.drop_column("food_item", "micros")
    op.drop_column("nutrition", "micros")
