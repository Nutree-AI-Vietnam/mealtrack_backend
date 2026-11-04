"""add users activity_calories

Revision ID: 20261103163630071544
Revises: 20261103044352264010
Create Date: 2026-11-03 23:36:30.158660

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261103163630071544"
down_revision: str | None = "20261103044352264010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "activity_calories",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "activity_calories")
