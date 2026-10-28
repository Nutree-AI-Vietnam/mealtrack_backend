"""add refunded to native subscription status enum

Revision ID: 20261028085152877654
Revises: 20261028084517715547
Create Date: 2026-10-28 08:51:52.914931

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261028085152877654"
down_revision: str | None = "20261028084517715547"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Migration 007 creates subscriptions.status as the native enum
# subscription_status_enum {active, expired, cancelled, billing_issue}; 053
# assumed the column was VARCHAR and never added 'refunded'. A database built
# from the models (create_all, as production's was) has VARCHAR and no such
# type, so this is a no-op there. The model's status values include
# 'refunded' (non-native Enum), and refunds write it.
ADD_REFUNDED = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_type
        WHERE oid = to_regtype('subscription_status_enum')
          AND typtype = 'e'
    ) THEN
        ALTER TYPE subscription_status_enum ADD VALUE IF NOT EXISTS 'refunded';
    END IF;
END
$$;
"""


def upgrade() -> None:
    """Let a database built by the Alembic chain store status 'refunded'.

    Outside the migration transaction, so the new value is committed (and
    usable) at once on any PostgreSQL version.
    """
    # Autogenerate also reported long-standing model/database drift
    # (native enums vs non-native in the model, indexes, timestamps); none of
    # it belongs to this change.
    with op.get_context().autocommit_block():
        op.execute(ADD_REFUNDED)


def downgrade() -> None:
    """No-op: PostgreSQL cannot drop a value from an enum type.

    Rows may already hold 'refunded', and the extra value is harmless to code
    that never writes it, so the type keeps it.
    """
