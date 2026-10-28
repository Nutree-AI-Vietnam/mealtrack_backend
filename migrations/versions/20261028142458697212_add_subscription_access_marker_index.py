"""add subscription access marker index

Revision ID: 20261028142458697212
Revises: 20261028101948171263
Create Date: 2026-10-28 14:24:58.729244

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261028142458697212"
down_revision: str | None = "20261028101948171263"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MARKED = (
    "provider_sync_required_at IS NOT NULL"
    " OR provider_refund_pending_at IS NOT NULL"
    " OR provider_transfer_source_at IS NOT NULL"
)


def upgrade() -> None:
    """Index only the subscriptions rows that carry a provider marker.

    GET /v1/health/subscription-access (polled every minute by the uptime
    monitor) and the support repair queue select exactly these rows. No
    other index covers the marker columns, so without this one each poll
    scans the whole table; with it, a poll reads the marked rows, normally
    none. Rows without a marker are not in the index, so it costs nothing
    on their writes.

    Autogenerate also reported the long-standing model/database drift noted
    in 20261026085224333692; none of it belongs to this change.
    """
    op.create_index(
        "ix_subscriptions_access_markers",
        "subscriptions",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text(MARKED),
    )


def downgrade() -> None:
    """Drop the partial index; the queries still work, by scanning."""
    op.drop_index(
        "ix_subscriptions_access_markers",
        table_name="subscriptions",
        postgresql_where=sa.text(MARKED),
    )
