"""remap two slot weekly plans to breakfast lunch dinner

Revision ID: 20261003160641390667
Revises: 20261003103219857350
Create Date: 2026-10-03 23:06:41.392510

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261003160641390667"
down_revision: str | None = "20261003103219857350"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Plans written by pre-breakfast servers after 20261003103219857350 ran still
# store lunch at 0 and dinner at 1. Such a plan has no slot 2.
_TWO_SLOT_PLANS = """
    SELECT plan_id
    FROM weekly_meal_plan_slots
    GROUP BY plan_id
    HAVING max(slot_index) < 2
"""


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TEMP TABLE _two_slot_plans ON COMMIT DROP AS {_TWO_SLOT_PLANS}
        """
    )
    op.execute(
        """
        UPDATE weekly_meal_plan_slots SET slot_index = 2
        WHERE slot_index = 1
          AND plan_id IN (SELECT plan_id FROM _two_slot_plans)
        """
    )
    op.execute(
        """
        UPDATE weekly_meal_plan_slots SET slot_index = 1
        WHERE slot_index = 0
          AND plan_id IN (SELECT plan_id FROM _two_slot_plans)
        """
    )
    op.execute(
        """
        INSERT INTO weekly_meal_plan_slots (
            id, plan_id, day_index, slot_index, is_logged, version,
            created_at, updated_at
        )
        SELECT gen_random_uuid()::text, plans.plan_id, days.day_index, 0,
               false, 1, now(), now()
        FROM _two_slot_plans AS plans
        CROSS JOIN generate_series(0, 6) AS days(day_index)
        WHERE NOT EXISTS (
            SELECT 1 FROM weekly_meal_plan_slots AS slots
            WHERE slots.plan_id = plans.plan_id
              AND slots.day_index = days.day_index
              AND slots.slot_index = 0
        )
        """
    )
    # Clients holding the old revision must reload instead of writing
    # lunch/dinner edits to the old coordinates.
    op.execute(
        """
        UPDATE weekly_meal_plans
        SET revision = revision + 1, updated_at = now()
        WHERE id IN (SELECT plan_id FROM _two_slot_plans)
        """
    )


def downgrade() -> None:
    # Forward-only data repair: after upgrade the remapped plans are
    # indistinguishable from plans created with three slots, and
    # 20261003103219857350's downgrade already restores the two-slot layout.
    pass
