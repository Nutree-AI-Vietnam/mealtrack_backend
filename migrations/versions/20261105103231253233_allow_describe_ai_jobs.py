"""Allow Ghi nhanh (describe) background AI jobs.

Widens ck_ai_jobs_job_type with describe_log, describe_movement and
describe_hydration. The new constraint is added NOT VALID and then
validated, so the table is not scanned under an exclusive lock; every
existing row already satisfies the wider list.

Revision ID: 20261105103231253233
Revises: 20261104095702973837
Create Date: 2026-11-05 10:32:31.287524

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261105103231253233"
down_revision: str | None = "20261104095702973837"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CONSTRAINT = "ck_ai_jobs_job_type"
_DESCRIBE_TYPES = ("describe_log", "describe_movement", "describe_hydration")
_OLD_TYPES = "'chat', 'meal_scan', 'parse_text', 'recipe_import', 'weekly_plan'"
_NEW_TYPES = _OLD_TYPES + ", " + ", ".join(f"'{t}'" for t in _DESCRIBE_TYPES)


def _replace_job_type_check(types: str) -> None:
    op.drop_constraint(_CONSTRAINT, "ai_jobs", type_="check")
    op.execute(
        sa.text(
            f"ALTER TABLE ai_jobs ADD CONSTRAINT {_CONSTRAINT} "
            f"CHECK (job_type IN ({types})) NOT VALID"
        )
    )
    op.execute(sa.text(f"ALTER TABLE ai_jobs VALIDATE CONSTRAINT {_CONSTRAINT}"))


def upgrade() -> None:
    _replace_job_type_check(_NEW_TYPES)


def downgrade() -> None:
    # A describe job's entries are already saved under its own replay key, so
    # only the job rows go; they cannot satisfy the narrower constraint.
    op.execute(
        sa.text("DELETE FROM ai_jobs WHERE job_type IN :types").bindparams(
            sa.bindparam("types", value=_DESCRIBE_TYPES, expanding=True)
        )
    )
    _replace_job_type_check(_OLD_TYPES)
