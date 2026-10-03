"""Reorder weekly slots to breakfast, lunch, and dinner.

Revision ID: 20261003103219857350
Revises: 20261002103128257288
Create Date: 2026-10-03 17:32:19.910935

"""

from __future__ import annotations

from alembic import op

revision = "20261003103219857350"
down_revision = "20261002103128257288"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_weekly_slot_slot_index", "weekly_meal_plan_slots", type_="check"
    )
    # Existing rows are lunch at 0 and dinner at 1. Move dinner first so the
    # unique coordinate is free, then move lunch, then add breakfast at 0.
    op.execute("UPDATE weekly_meal_plan_slots SET slot_index = 2 WHERE slot_index = 1")
    op.execute("UPDATE weekly_meal_plan_slots SET slot_index = 1 WHERE slot_index = 0")
    op.execute(
        """
        INSERT INTO weekly_meal_plan_slots (
            id,
            plan_id,
            day_index,
            slot_index,
            is_logged,
            version,
            created_at,
            updated_at
        )
        SELECT
            gen_random_uuid()::text,
            plans.id,
            days.day_index,
            0,
            false,
            1,
            now(),
            now()
        FROM weekly_meal_plans AS plans
        CROSS JOIN generate_series(0, 6) AS days(day_index)
        WHERE NOT EXISTS (
            SELECT 1
            FROM weekly_meal_plan_slots AS slots
            WHERE slots.plan_id = plans.id
              AND slots.day_index = days.day_index
              AND slots.slot_index = 0
        )
        """
    )
    op.create_check_constraint(
        "ck_weekly_slot_slot_index",
        "weekly_meal_plan_slots",
        "slot_index BETWEEN 0 AND 2",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_weekly_slot_slot_index", "weekly_meal_plan_slots", type_="check"
    )
    op.execute("DELETE FROM weekly_meal_plan_slots WHERE slot_index = 0")
    op.execute("UPDATE weekly_meal_plan_slots SET slot_index = 0 WHERE slot_index = 1")
    op.execute("UPDATE weekly_meal_plan_slots SET slot_index = 1 WHERE slot_index = 2")
    op.create_check_constraint(
        "ck_weekly_slot_slot_index",
        "weekly_meal_plan_slots",
        "slot_index BETWEEN 0 AND 1",
    )
