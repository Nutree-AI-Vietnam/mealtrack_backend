"""Add weight loss pace to user profiles.

Revision ID: 20261014140000000000
Revises: 20261028161112375733
Create Date: 2026-10-14 14:00:00

"""

import sqlalchemy as sa
from alembic import op

revision = "20261014140000000000"
down_revision = "20261028161112375733"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("weight_loss_kg_per_week", sa.Float(), nullable=True),
    )
    op.add_column(
        "user_profiles",
        sa.Column(
            "pace_clinician_disclaimer",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "user_profiles",
        sa.Column("weight_loss_pace_reset_on", sa.Date(), nullable=True),
    )
    op.create_check_constraint(
        "check_weight_loss_kg_per_week",
        "user_profiles",
        "weight_loss_kg_per_week IS NULL "
        "OR (weight_loss_kg_per_week > 0 AND weight_loss_kg_per_week <= 1)",
    )


def downgrade() -> None:
    op.drop_constraint("check_weight_loss_kg_per_week", "user_profiles", type_="check")
    op.drop_column("user_profiles", "weight_loss_pace_reset_on")
    op.drop_column("user_profiles", "pace_clinician_disclaimer")
    op.drop_column("user_profiles", "weight_loss_kg_per_week")
