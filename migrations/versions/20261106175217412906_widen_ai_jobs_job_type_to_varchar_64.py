"""Widen ai_jobs.job_type so describe_hydration can be inserted.

describe_hydration is 18 characters and describe_movement is 17. The column
was created as varchar(16), so Postgres rejected those inserts with
StringDataRightTruncation (Sentry PYTHON-KZ) before the job-type check
constraint ran. Widening varchar(16) to varchar(64) changes only the
typmod: Postgres takes a brief ACCESS EXCLUSIVE lock and does not rewrite
the table or its data.

Downgrade narrows the column back to varchar(16) only when every stored
job_type still fits. If any value is longer, Postgres aborts the ALTER
with "value too long for type character varying(16)" and leaves the rows
in place. This downgrade does not delete or truncate those rows.

Revision ID: 20261106175217412906
Revises: 20261105103231253233
Create Date: 2026-11-06 17:52:17.486270

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261106175217412906"
down_revision: str | None = "20261105103231253233"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Typmod-only change (varchar(16) -> varchar(64)). No USING clause, so
    # Postgres does not rewrite ai_jobs.
    op.alter_column(
        "ai_jobs",
        "job_type",
        existing_type=sa.String(length=16),
        type_=sa.String(length=64),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Fails if any job_type is longer than 16 characters (describe_hydration
    # is 18, describe_movement is 17). Postgres raises StringDataRightTruncation
    # and does not truncate or delete the longer values.
    op.alter_column(
        "ai_jobs",
        "job_type",
        existing_type=sa.String(length=64),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
