"""index notifications by type for dispatcher scans

Revision ID: 20261107080508073817
Revises: 20261106175217412906
Create Date: 2026-11-07 08:05:08.077139

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20261107080508073817"
down_revision: Union[str, None] = "20261106175217412906"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The push dispatcher filters on its own notification types, and the older
# indexes start at scheduled_for_utc / expires_at, so every tick walked every
# pending row of every type - including ~24k retired reminders left pending
# since September - to find a handful of billing pushes.
# Both scans - claiming due pushes and expiring stale ones - use this one
# index; measured on a copy of that data, each read under ten blocks instead
# of 2,600-11,500.
_INDEXES = (
    (
        "idx_notifications_type_open_expiry",
        ["notification_type", "expires_at"],
        "status IN ('pending', 'processing')",
    ),
)


def upgrade() -> None:
    # CONCURRENTLY so the build never blocks notification writes, which cannot
    # run inside the migration transaction. A failed build leaves an invalid
    # index; drop it first so a retry can build a valid one.
    with op.get_context().autocommit_block():
        for name, columns, where in _INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            op.create_index(
                name,
                "notifications",
                columns,
                unique=False,
                postgresql_where=sa.text(where),
                postgresql_concurrently=True,
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, _, _ in reversed(_INDEXES):
            op.drop_index(
                name,
                table_name="notifications",
                postgresql_concurrently=True,
                if_exists=True,
            )
