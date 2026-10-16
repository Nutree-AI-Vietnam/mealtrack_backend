"""Add entitlement ledger tables (NM-680).

Five additive tables for the backend entitlement ledger: billing customer
identities, the provider webhook inbox, store subscriptions, manual grants and
the user entitlement projection. Legacy ``subscriptions`` is untouched.

Autogenerate also reported unrelated pre-existing model/schema drift; only the
five new tables are kept here. Every constraint and partial index below is also
declared on the ORM models, which CI uses through ``create_all``.

Revision ID: 20261015184814225029
Revises: 20261015014451225604
Create Date: 2026-10-15 18:48:14.268714

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20261015184814225029"
down_revision: str | None = "20261015014451225604"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "billing_customer_identities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column(
            "project", sa.String(length=64), server_default="default", nullable=False
        ),
        sa.Column("provider_customer_id", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('primary', 'alias', 'anonymous', 'legacy_lead')",
            name="ck_bci_kind",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "project",
            "provider_customer_id",
            name="uq_bci_provider_customer",
        ),
    )
    op.create_index(
        "ix_bci_user", "billing_customer_identities", ["user_id"], unique=False
    )
    op.create_index(
        "uq_bci_one_primary",
        "billing_customer_identities",
        ["user_id", "provider", "project"],
        unique=True,
        postgresql_where=sa.text("kind = 'primary' AND retired_at IS NULL"),
    )
    op.create_table(
        "billing_provider_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=True),
        sa.Column("app_user_id", sa.String(length=255), nullable=True),
        sa.Column("event_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "raw",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("park_reason", sa.String(length=32), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('received', 'processing', 'processed', 'unmatched', 'retry', 'ignored', 'dead')",
            name="ck_bpe_status",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_event_id", name="uq_bpe_event"),
    )
    op.create_index(
        "ix_bpe_due",
        "billing_provider_events",
        ["next_attempt_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('received', 'retry')"),
    )
    op.create_index(
        "ix_bpe_unmatched",
        "billing_provider_events",
        ["received_at"],
        unique=False,
        postgresql_where=sa.text("status = 'unmatched'"),
    )
    op.create_index(
        "ix_bpe_user",
        "billing_provider_events",
        ["user_id", "received_at"],
        unique=False,
    )
    op.create_index(
        "ix_bpe_lease_expiry",
        "billing_provider_events",
        ["lease_expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'processing'"),
    )
    op.create_index(
        "ix_bpe_unmatched_app_user",
        "billing_provider_events",
        ["app_user_id"],
        unique=False,
        postgresql_where=sa.text("status = 'unmatched'"),
    )
    op.create_table(
        "entitlement_grants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "entitlement_key",
            sa.String(length=64),
            server_default="standard",
            nullable=False,
        ),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("source_ref", sa.String(length=255), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=255), nullable=True),
        sa.Column("revoked_by", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("rc_mirror_ref", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "ends_at <= starts_at + interval '366 days'", name="ck_eg_window_max"
        ),
        sa.CheckConstraint(
            "source IN ('admin', 'support', 'qa', 'migration')", name="ck_eg_source"
        ),
        sa.CheckConstraint("ends_at > starts_at", name="ck_eg_window_positive"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_eg_user_live",
        "entitlement_grants",
        ["user_id", "entitlement_key"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "uq_eg_source",
        "entitlement_grants",
        ["source", "source_ref", "entitlement_key"],
        unique=True,
        postgresql_where=sa.text("source_ref IS NOT NULL"),
    )
    op.create_table(
        "store_subscriptions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("store", sa.String(length=24), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("subscription_key", sa.String(length=255), nullable=False),
        sa.Column("source_customer_id", sa.String(length=255), nullable=False),
        sa.Column("store_identifier", sa.String(length=255), nullable=True),
        sa.Column("original_transaction_id", sa.String(length=255), nullable=True),
        sa.Column("product_id", sa.String(length=255), nullable=False),
        sa.Column("entitlement_key", sa.String(length=64), nullable=True),
        sa.Column("ownership_type", sa.String(length=16), nullable=True),
        sa.Column("period_type", sa.String(length=16), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("will_renew", sa.Boolean(), nullable=True),
        sa.Column(
            "is_lifetime", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grace_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unsubscribe_detected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "billing_issue_detected_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("absent_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "environment IN ('production', 'sandbox')", name="ck_ss_environment"
        ),
        sa.CheckConstraint(
            "state IN ('trialing', 'active', 'in_grace', 'billing_retry', 'paused', 'expired', 'refunded', 'revoked', 'absent')",
            name="ck_ss_state",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "environment", "subscription_key", name="uq_ss_key"
        ),
    )
    op.create_index(
        "ix_ss_customer",
        "store_subscriptions",
        ["provider", "source_customer_id"],
        unique=False,
    )
    op.create_index("ix_ss_expiry", "store_subscriptions", ["expires_at"], unique=False)
    op.create_index("ix_ss_user", "store_subscriptions", ["user_id"], unique=False)
    op.create_table(
        "user_entitlements",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("entitlement_key", sa.String(length=64), nullable=False),
        sa.Column("has_access", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("access_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_lifetime", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("next_change_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("will_renew", sa.Boolean(), nullable=True),
        sa.Column("period_type", sa.String(length=16), nullable=True),
        sa.Column("primary_source", sa.String(length=8), nullable=False),
        sa.Column("primary_ref", sa.String(length=255), nullable=True),
        sa.Column("product_id", sa.String(length=255), nullable=True),
        sa.Column("store", sa.String(length=24), nullable=True),
        sa.Column(
            "is_sandbox", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "ever_had_access",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_provider_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_purchase_hint_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "primary_source IN ('store', 'grant')", name="ck_ue_primary_source"
        ),
        sa.CheckConstraint(
            "state IN ('trial', 'active', 'grace', 'cancelled_active', 'expired', 'none')",
            name="ck_ue_state",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "entitlement_key", name="pk_user_entitlements"
        ),
    )
    op.create_index(
        "ix_ue_next_change", "user_entitlements", ["next_change_at"], unique=False
    )


def downgrade() -> None:
    # Additive tables only: dropping them loses no legacy data.
    op.drop_index("ix_ue_next_change", table_name="user_entitlements")
    op.drop_table("user_entitlements")
    op.drop_index("ix_ss_user", table_name="store_subscriptions")
    op.drop_index("ix_ss_expiry", table_name="store_subscriptions")
    op.drop_index("ix_ss_customer", table_name="store_subscriptions")
    op.drop_table("store_subscriptions")
    op.drop_index(
        "uq_eg_source",
        table_name="entitlement_grants",
        postgresql_where=sa.text("source_ref IS NOT NULL"),
    )
    op.drop_index(
        "ix_eg_user_live",
        table_name="entitlement_grants",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_table("entitlement_grants")
    op.drop_index(
        "ix_bpe_unmatched_app_user",
        table_name="billing_provider_events",
        postgresql_where=sa.text("status = 'unmatched'"),
    )
    op.drop_index(
        "ix_bpe_lease_expiry",
        table_name="billing_provider_events",
        postgresql_where=sa.text("status = 'processing'"),
    )
    op.drop_index("ix_bpe_user", table_name="billing_provider_events")
    op.drop_index(
        "ix_bpe_unmatched",
        table_name="billing_provider_events",
        postgresql_where=sa.text("status = 'unmatched'"),
    )
    op.drop_index(
        "ix_bpe_due",
        table_name="billing_provider_events",
        postgresql_where=sa.text("status IN ('received', 'retry')"),
    )
    op.drop_table("billing_provider_events")
    op.drop_index(
        "uq_bci_one_primary",
        table_name="billing_customer_identities",
        postgresql_where=sa.text("kind = 'primary' AND retired_at IS NULL"),
    )
    op.drop_index("ix_bci_user", table_name="billing_customer_identities")
    op.drop_table("billing_customer_identities")
