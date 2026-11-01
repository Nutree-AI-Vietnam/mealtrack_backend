"""Allow recipe_import background AI jobs.

Widens ck_ai_jobs_job_type. The new constraint is added NOT VALID and then
validated, so the table is not scanned under an exclusive lock; every
existing row already satisfies the wider list.

Revision ID: 20261101091406080591
Revises: 20261014140000000000
Create Date: 2026-11-01 09:14:06.121762

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261101091406080591"
down_revision: str | None = "20261014140000000000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CONSTRAINT = "ck_ai_jobs_job_type"
_OLD_TYPES = "'chat', 'meal_scan', 'parse_text'"
_NEW_TYPES = "'chat', 'meal_scan', 'parse_text', 'recipe_import'"


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
    # Recipe import jobs are transient drafts; they cannot satisfy the
    # narrower constraint, so they are removed before it is restored.
    op.execute(sa.text("DELETE FROM ai_jobs WHERE job_type = 'recipe_import'"))
    _replace_job_type_check(_OLD_TYPES)
