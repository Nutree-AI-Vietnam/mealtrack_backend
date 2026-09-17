"""Add silent-login mint consume columns to web_funnel_redemptions.

Revision ID: 20260917000001
Revises: 20260915035556051174
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917000001"
down_revision: str | None = "20260915035556051174"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "web_funnel_redemptions",
        sa.Column("silent_login_minted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "web_funnel_redemptions",
        sa.Column(
            "silent_login_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("web_funnel_redemptions", "silent_login_generation")
    op.drop_column("web_funnel_redemptions", "silent_login_minted_at")
