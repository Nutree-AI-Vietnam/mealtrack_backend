"""Allow weekly_plan AI jobs and a plan that is still being written.

Two widened check constraints, nothing else. A weekly plan is about to be
filled by a model rather than picked from the catalogue, which takes longer
than a request may wait: the work becomes a background job, and the plan
row exists in 'preparing' while that job runs.

Both constraints are added NOT VALID and then validated, so neither table is
scanned under an exclusive lock; every existing row already satisfies the
wider list.

Revision ID: 20261104095702973837
Revises: 20261103103408775030
Create Date: 2026-11-04 09:57:02

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261104095702973837"
down_revision: str | None = "20261104041432763875"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_JOB_CONSTRAINT = "ck_ai_jobs_job_type"
_OLD_JOB_TYPES = "'chat', 'meal_scan', 'parse_text', 'recipe_import'"
_NEW_JOB_TYPES = "'chat', 'meal_scan', 'parse_text', 'recipe_import', 'weekly_plan'"

_PLAN_CONSTRAINT = "ck_weekly_meal_plan_status"
_OLD_PLAN_STATUSES = "'draft', 'confirmed'"
_NEW_PLAN_STATUSES = "'draft', 'confirmed', 'preparing'"


def _replace_check(table: str, constraint: str, expression: str) -> None:
    op.drop_constraint(constraint, table, type_="check")
    op.execute(
        sa.text(
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
            f"CHECK ({expression}) NOT VALID"
        )
    )
    op.execute(sa.text(f"ALTER TABLE {table} VALIDATE CONSTRAINT {constraint}"))


def upgrade() -> None:
    _replace_check("ai_jobs", _JOB_CONSTRAINT, f"job_type IN ({_NEW_JOB_TYPES})")
    _replace_check(
        "weekly_meal_plans", _PLAN_CONSTRAINT, f"status IN ({_NEW_PLAN_STATUSES})"
    )


def downgrade() -> None:
    # A plan still being written has no dishes to keep, and the job that was
    # writing it cannot satisfy the narrower constraint either. Both go
    # before the constraints are restored; the user asks for the week again.
    op.execute(sa.text("DELETE FROM weekly_meal_plans WHERE status = 'preparing'"))
    op.execute(sa.text("DELETE FROM ai_jobs WHERE job_type = 'weekly_plan'"))
    _replace_check(
        "weekly_meal_plans", _PLAN_CONSTRAINT, f"status IN ({_OLD_PLAN_STATUSES})"
    )
    _replace_check("ai_jobs", _JOB_CONSTRAINT, f"job_type IN ({_OLD_JOB_TYPES})")
