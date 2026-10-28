"""add subscription access metadata

Revision ID: 20261022125800304190
Revises: 20261013115453616929
Create Date: 2026-10-22 19:58:00.318205

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261022125800304190"
down_revision: str | None = "20261013115453616929"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add normalized provider facts used by the shared access policy."""
    op.add_column(
        "subscriptions",
        sa.Column(
            "provider",
            sa.String(length=32),
            server_default=sa.text("'revenuecat'"),
            nullable=False,
        ),
    )
    op.add_column(
        "subscriptions",
        sa.Column("grace_period_expires_at", sa.DateTime(timezone=True)),
    )
    op.add_column("subscriptions", sa.Column("period_type", sa.String(length=16)))
    op.add_column(
        "subscriptions",
        sa.Column("provider_snapshot_at", sa.DateTime(timezone=True)),
    )


# Dropping ``provider`` makes every row read as RevenueCat's again: the image
# before this revision would expire or overwrite another provider's row on
# its owner's RevenueCat events. Refuse while any exists.
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
            'Refusing to downgrade 20261022125800304190: % subscriptions rows '
            'belong to providers other than RevenueCat. Dropping provider '
            'would make them RevenueCat rows. Export and remove them first, '
            'or keep this revision.',
            blocking;
    END IF;
END
$$;
"""


def downgrade() -> None:
    """Remove the additive provider facts, refusing while other providers'
    rows exist (they would read as RevenueCat's without ``provider``)."""
    op.execute(REFUSE_OTHER_PROVIDERS)
    op.drop_column("subscriptions", "provider_snapshot_at")
    op.drop_column("subscriptions", "period_type")
    op.drop_column("subscriptions", "grace_period_expires_at")
    op.drop_column("subscriptions", "provider")
