"""make revenuecat subscriber id nullable for other providers

Revision ID: 20261026085224333692
Revises: 20261026080322454705
Create Date: 2026-10-26 08:52:24.384378

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261026085224333692"
down_revision: str | None = "20261026080322454705"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Let rows from providers other than RevenueCat leave the RevenueCat id NULL.

    Autogenerate also reported long-standing model/database drift on other
    tables and on subscriptions.platform/status (native enums in the
    database, non-native in the model); none of it belongs to this change,
    so only the nullability change is kept.
    """
    op.alter_column(
        "subscriptions",
        "revenuecat_subscriber_id",
        existing_type=sa.VARCHAR(length=255),
        nullable=True,
    )


def downgrade() -> None:
    """Restore NOT NULL without losing rows another provider wrote.

    Such rows get the owner's Firebase uid (the value RevenueCat rows use),
    or the user id when there is none (the user_id foreign key guarantees
    the join). RevenueCat code filters on provider='revenuecat', so the
    backfilled id never matches them.
    """
    op.execute(
        """
        UPDATE subscriptions AS s
        SET revenuecat_subscriber_id = COALESCE(u.firebase_uid, s.user_id)
        FROM users AS u
        WHERE u.id = s.user_id
          AND s.revenuecat_subscriber_id IS NULL
        """
    )
    op.alter_column(
        "subscriptions",
        "revenuecat_subscriber_id",
        existing_type=sa.VARCHAR(length=255),
        nullable=False,
    )
