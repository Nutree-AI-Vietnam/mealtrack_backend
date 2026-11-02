"""cover streak log day scan

Revision ID: 20261102140358147160
Revises: 20261101105647127919
Create Date: 2026-11-02 14:03:58.166973

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261102140358147160"
down_revision: Union[str, None] = "20261101105647127919"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "ix_meal_streak_user_created"


def upgrade() -> None:
    # CONCURRENTLY cannot run inside the migration transaction. A failed build
    # leaves an invalid index; drop it first so a retry can build a valid one.
    # Raise the statement timeout for this statement only: the session default
    # (240s) can cancel a build on a large meal table and leave that invalid
    # index behind.
    previous_timeout = op.get_bind().exec_driver_sql("SHOW statement_timeout").scalar()
    with op.get_context().autocommit_block():
        op.execute("SET statement_timeout = 0")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_meal_streak_user_created")
        op.create_index(
            _INDEX,
            "meal",
            ["user_id", "created_at"],
            unique=False,
            postgresql_include=["logged_on_day", "status", "ready_at", "meal_id"],
            postgresql_where=sa.text("status <> 'INACTIVE'"),
            postgresql_concurrently=True,
        )
        op.execute(f"SET statement_timeout = '{previous_timeout}'")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_meal_streak_user_created",
            table_name="meal",
            postgresql_concurrently=True,
            if_exists=True,
        )
