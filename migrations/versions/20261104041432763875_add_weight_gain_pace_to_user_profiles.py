"""add weight gain pace to user profiles

Revision ID: 20261104041432763875
Revises: 20261103163630071544
Create Date: 2026-11-04 11:14:32.776888

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261104041432763875"
down_revision: str | None = "20261103163630071544"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("weight_gain_kg_per_week", sa.Float(), nullable=True),
    )
    op.create_check_constraint(
        "check_weight_gain_kg_per_week",
        "user_profiles",
        "weight_gain_kg_per_week IS NULL "
        "OR (weight_gain_kg_per_week > 0 AND weight_gain_kg_per_week <= 1)",
    )


def downgrade() -> None:
    op.drop_constraint("check_weight_gain_kg_per_week", "user_profiles", type_="check")
    op.drop_column("user_profiles", "weight_gain_kg_per_week")
