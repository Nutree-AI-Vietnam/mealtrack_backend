"""repair late legacy two slot weekly plans

Revision ID: 20261006115245714931
Revises: 20261005022758771114
Create Date: 2026-10-06 18:52:45.715591

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261006115245714931"
down_revision: str | None = "20261005022758771114"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Keep generated breakfast IDs and prior plan state so only this repair's
    # rows can be removed by a later downgrade.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS weekly_plan_late_legacy_slot_repairs (
            plan_id varchar(36) NOT NULL REFERENCES weekly_meal_plans(id)
                ON DELETE CASCADE,
            day_index integer NOT NULL CHECK (day_index BETWEEN 0 AND 6),
            breakfast_slot_id varchar(36) NOT NULL UNIQUE,
            original_revision integer NOT NULL,
            original_updated_at timestamptz,
            PRIMARY KEY (plan_id, day_index)
        )
        """
    )
    op.execute(
        "LOCK TABLE weekly_meal_plans, weekly_meal_plan_slots "
        "IN SHARE ROW EXCLUSIVE MODE"
    )
    op.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS _late_legacy_repair_candidates (
            plan_id varchar(36) PRIMARY KEY,
            original_revision integer NOT NULL,
            original_updated_at timestamptz
        ) ON COMMIT DROP
        """
    )
    op.execute("TRUNCATE _late_legacy_repair_candidates")
    # The unique coordinate constraint makes fourteen rows with seven of
    # each index exactly one lunch/dinner pair per day.
    op.execute(
        """
        INSERT INTO _late_legacy_repair_candidates
            (plan_id, original_revision, original_updated_at)
        SELECT p.id, p.revision, p.updated_at
        FROM weekly_meal_plans AS p
        JOIN weekly_meal_plan_slots AS s ON s.plan_id = p.id
        WHERE p.algorithm_version = 'v1'
          AND p.status = 'draft'
          AND NOT EXISTS (
              SELECT 1 FROM weekly_plan_late_legacy_slot_repairs AS done
              WHERE done.plan_id = p.id
          )
        GROUP BY p.id, p.revision, p.updated_at
        HAVING count(*) = 14
           AND count(DISTINCT s.day_index) = 7
           AND count(*) FILTER (WHERE s.slot_index = 0) = 7
           AND count(*) FILTER (WHERE s.slot_index = 1) = 7
           AND count(*) FILTER (WHERE s.slot_index = 2) = 0
        """
    )
    op.execute(
        """
        INSERT INTO weekly_plan_late_legacy_slot_repairs
            (plan_id, day_index, breakfast_slot_id,
             original_revision, original_updated_at)
        SELECT c.plan_id, days.day_index, gen_random_uuid()::text,
               c.original_revision, c.original_updated_at
        FROM _late_legacy_repair_candidates AS c
        CROSS JOIN generate_series(0, 6) AS days(day_index)
        """
    )
    # Shift dinner before lunch to keep the unique plan/day/slot key free.
    op.execute(
        """
        UPDATE weekly_meal_plan_slots SET slot_index = 2
        WHERE slot_index = 1
          AND plan_id IN (SELECT plan_id FROM _late_legacy_repair_candidates)
        """
    )
    op.execute(
        """
        UPDATE weekly_meal_plan_slots SET slot_index = 1
        WHERE slot_index = 0
          AND plan_id IN (SELECT plan_id FROM _late_legacy_repair_candidates)
        """
    )
    op.execute(
        """
        INSERT INTO weekly_meal_plan_slots
            (id, plan_id, day_index, slot_index, is_logged,
             version, created_at, updated_at)
        SELECT breakfast_slot_id, plan_id, day_index, 0,
               false, 1, now(), now()
        FROM weekly_plan_late_legacy_slot_repairs
        WHERE plan_id IN (SELECT plan_id FROM _late_legacy_repair_candidates)
        """
    )
    op.execute(
        """
        UPDATE weekly_meal_plans AS p
        SET revision = p.revision + 1, updated_at = now()
        FROM _late_legacy_repair_candidates AS c
        WHERE p.id = c.plan_id
        """
    )


def downgrade() -> None:
    connection = op.get_bind()
    if (
        connection.execute(
            sa.text(
                "SELECT to_regclass(format('%I.%I', current_schema(), "
                "'weekly_plan_late_legacy_slot_repairs'))"
            )
        ).scalar()
        is None
    ):
        return

    op.execute(
        "LOCK TABLE weekly_meal_plans, weekly_meal_plan_slots "
        "IN SHARE ROW EXCLUSIVE MODE"
    )
    # Refuse downgrade after an edit rather than deleting a user's breakfast
    # or rolling a later revision backward.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM weekly_plan_late_legacy_slot_repairs AS marker
                LEFT JOIN weekly_meal_plans AS p ON p.id = marker.plan_id
                LEFT JOIN weekly_meal_plan_slots AS breakfast
                    ON breakfast.id = marker.breakfast_slot_id
                WHERE p.id IS NULL
                   OR p.revision <> marker.original_revision + 1
                   OR breakfast.id IS NULL
                   OR breakfast.plan_id <> marker.plan_id
                   OR breakfast.day_index <> marker.day_index
                   OR breakfast.slot_index <> 0
                   OR breakfast.catalog_meal_id IS NOT NULL
                   OR breakfast.is_logged
                   OR breakfast.logged_meal_id IS NOT NULL
                   OR breakfast.version <> 1
            ) OR EXISTS (
                SELECT 1
                FROM weekly_plan_late_legacy_slot_repairs AS marker
                JOIN weekly_meal_plan_slots AS slots
                    ON slots.plan_id = marker.plan_id
                GROUP BY marker.plan_id
                HAVING count(DISTINCT (slots.day_index, slots.slot_index)) <> 21
            ) THEN
                RAISE EXCEPTION 'Cannot reverse a changed weekly plan slot repair';
            END IF;
        END $$
        """
    )
    op.execute(
        """
        DELETE FROM weekly_meal_plan_slots AS slots
        USING weekly_plan_late_legacy_slot_repairs AS marker
        WHERE slots.id = marker.breakfast_slot_id
        """
    )
    op.execute(
        """
        UPDATE weekly_meal_plan_slots SET slot_index = 0
        WHERE slot_index = 1
          AND plan_id IN (SELECT plan_id FROM weekly_plan_late_legacy_slot_repairs)
        """
    )
    op.execute(
        """
        UPDATE weekly_meal_plan_slots SET slot_index = 1
        WHERE slot_index = 2
          AND plan_id IN (SELECT plan_id FROM weekly_plan_late_legacy_slot_repairs)
        """
    )
    op.execute(
        """
        UPDATE weekly_meal_plans AS p
        SET revision = marker.original_revision,
            updated_at = marker.original_updated_at
        FROM (
            SELECT DISTINCT ON (plan_id)
                plan_id, original_revision, original_updated_at
            FROM weekly_plan_late_legacy_slot_repairs
        ) AS marker
        WHERE p.id = marker.plan_id
        """
    )
    op.execute("DROP TABLE weekly_plan_late_legacy_slot_repairs")
