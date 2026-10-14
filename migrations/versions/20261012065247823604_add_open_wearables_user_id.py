"""add open wearables user id

Revision ID: 20261012065247823604
Revises: 20261006115245714931
Create Date: 2026-10-12 13:52:48.135078

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261012065247823604"
down_revision: str | None = "20261006115245714931"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("open_wearables_user_id", sa.String(length=36), nullable=True),
    )
    op.create_unique_constraint(
        "uq_users_open_wearables_user_id",
        "users",
        ["open_wearables_user_id"],
    )


def downgrade() -> None:
    # A schema built from the models (create_all + stamp) names this constraint
    # users_open_wearables_user_id_key, so drop by name only if it exists.
    # Dropping the column removes whichever unique constraint is left.
    op.execute(
        "ALTER TABLE users DROP CONSTRAINT IF EXISTS uq_users_open_wearables_user_id"
    )
    op.drop_column("users", "open_wearables_user_id")
