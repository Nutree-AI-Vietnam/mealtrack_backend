"""widen food_item unit to 120

Revision ID: 20261004073003287646
Revises: 20261003160641390667
Create Date: 2026-10-04 14:30:03.289866

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20261004073003287646"
down_revision: Union[str, None] = "20261003160641390667"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Serving-size names (food_reference_serving_sizes.name, up to 100 chars)
    # are copied into food_item.unit when a user picks an allowed unit.
    op.alter_column(
        "food_item",
        "unit",
        existing_type=sa.String(length=50),
        type_=sa.String(length=120),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.execute("UPDATE food_item SET unit = left(unit, 50) WHERE length(unit) > 50")
    op.alter_column(
        "food_item",
        "unit",
        existing_type=sa.String(length=120),
        type_=sa.String(length=50),
        existing_nullable=False,
    )
