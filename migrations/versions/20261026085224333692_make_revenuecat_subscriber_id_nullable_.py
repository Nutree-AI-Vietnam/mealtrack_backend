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


# The image before this revision looks RevenueCat rows up by
# revenuecat_subscriber_id alone (no provider filter, no ORDER BY), so any
# row of another provider is at risk once it is back: filling one with the
# owner's Firebase uid, or one already carrying an id, lets that user's
# RevenueCat events select and overwrite it. Refuse while any exists.
REFUSE_OTHER_PROVIDERS = """
DO $$
DECLARE
    blocking integer;
BEGIN
    SELECT count(*) INTO blocking
    FROM subscriptions
    WHERE provider <> 'revenuecat';
    IF blocking > 0 THEN
        RAISE EXCEPTION
            'Refusing to downgrade 20261026085224333692: % subscriptions rows '
            'belong to providers other than RevenueCat. The image before this '
            'revision would treat them as RevenueCat rows. Export and remove '
            'them first, or keep this revision.',
            blocking;
    END IF;
END
$$;
"""


def downgrade() -> None:
    """Restore NOT NULL, refusing while any other provider's row exists.

    A RevenueCat row with no id (a user who had no Firebase uid when it was
    written) gets the owner's Firebase uid, the value RevenueCat rows use, or
    the user id when there is none (the user_id foreign key guarantees the
    join). Rows of other providers (MoMo) are never backfilled or left for
    the older image: the migration raises while any exists, with or without
    an id.
    """
    op.execute(REFUSE_OTHER_PROVIDERS)
    op.execute(
        """
        UPDATE subscriptions AS s
        SET revenuecat_subscriber_id = COALESCE(u.firebase_uid, s.user_id)
        FROM users AS u
        WHERE u.id = s.user_id
          AND s.revenuecat_subscriber_id IS NULL
          AND s.provider = 'revenuecat'
        """
    )
    op.alter_column(
        "subscriptions",
        "revenuecat_subscriber_id",
        existing_type=sa.VARCHAR(length=255),
        nullable=False,
    )
