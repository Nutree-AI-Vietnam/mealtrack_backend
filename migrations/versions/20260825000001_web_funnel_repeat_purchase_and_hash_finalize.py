"""Allow repeat purchases per UID; index hash+preflight finalize path.

Revision ID: 20260825000001
Revises: 20260823000002
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260825000001"
down_revision: str | None = "20260823000002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # One Firebase UID may finalize multiple distinct purchases over time.
    op.drop_constraint(
        "web_funnel_redemptions_finalized_uid_key",
        "web_funnel_redemptions",
        type_="unique",
    )
    op.drop_constraint(
        "web_funnel_redemptions_redeemer_uid_key",
        "web_funnel_redemptions",
        type_="unique",
    )
    op.create_index(
        "ix_web_funnel_redemptions_redeemer_uid",
        "web_funnel_redemptions",
        ["redeemer_uid"],
    )
    op.create_index(
        "ix_web_funnel_redemptions_preflight_uid_hash",
        "web_funnel_redemptions",
        ["preflight_uid", "redemption_link_hash"],
    )

    # Same UID may claim multiple leads (repeat purchase / new checkout).
    op.drop_constraint(
        "web_funnel_leads_claimed_uid_key",
        "web_funnel_leads",
        type_="unique",
    )
    op.create_index(
        "ix_web_funnel_leads_claimed_uid",
        "web_funnel_leads",
        ["claimed_uid"],
    )


def downgrade() -> None:
    op.drop_index("ix_web_funnel_leads_claimed_uid", table_name="web_funnel_leads")
    op.create_unique_constraint(
        "web_funnel_leads_claimed_uid_key",
        "web_funnel_leads",
        ["claimed_uid"],
    )
    op.drop_index(
        "ix_web_funnel_redemptions_preflight_uid_hash",
        table_name="web_funnel_redemptions",
    )
    op.drop_index(
        "ix_web_funnel_redemptions_redeemer_uid",
        table_name="web_funnel_redemptions",
    )
    op.create_unique_constraint(
        "web_funnel_redemptions_redeemer_uid_key",
        "web_funnel_redemptions",
        ["redeemer_uid"],
    )
    op.create_unique_constraint(
        "web_funnel_redemptions_finalized_uid_key",
        "web_funnel_redemptions",
        ["finalized_uid"],
    )
