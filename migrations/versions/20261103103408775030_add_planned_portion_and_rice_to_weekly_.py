"""add planned portion and rice to weekly slots

A composed plan says how much of a dish to serve and how much rice goes with
it. Both are written on every row, so plans made before composing read as one
plain serving with no rice.

Revision ID: 20261103103408775030
Revises: 20261103163630071544
Create Date: 2026-11-03

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261103103408775030"
down_revision: str | None = "20261103163630071544"
branch_labels: str | sa.Sequence[str] | None = None
depends_on: str | sa.Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "weekly_meal_plan_slots",
        sa.Column(
            "planned_portion",
            sa.Numeric(precision=3, scale=2),
            server_default="1",
            nullable=False,
        ),
    )
    op.add_column(
        "weekly_meal_plan_slots",
        sa.Column(
            "planned_rice_steps",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    # A portion the slot-log endpoint accepts, and at most two bowls of rice.
    op.create_check_constraint(
        "ck_weekly_slot_planned_portion",
        "weekly_meal_plan_slots",
        "planned_portion IN (0.5, 1, 1.5, 2)",
    )
    op.create_check_constraint(
        "ck_weekly_slot_planned_rice_steps",
        "weekly_meal_plan_slots",
        "planned_rice_steps BETWEEN 0 AND 4",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_weekly_slot_planned_rice_steps", "weekly_meal_plan_slots", type_="check"
    )
    op.drop_constraint(
        "ck_weekly_slot_planned_portion", "weekly_meal_plan_slots", type_="check"
    )
    op.drop_column("weekly_meal_plan_slots", "planned_rice_steps")
    op.drop_column("weekly_meal_plan_slots", "planned_portion")
