"""Remove the retired food-item unit metadata column."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260823000002"
down_revision: str | None = "20260823000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("food_item"):
        columns = {column["name"] for column in inspector.get_columns("food_item")}
        if "allowed_units" in columns:
            op.drop_column("food_item", "allowed_units")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("food_item"):
        columns = {column["name"] for column in inspector.get_columns("food_item")}
        if "allowed_units" not in columns:
            op.add_column("food_item", sa.Column("allowed_units", sa.JSON(), nullable=True))
