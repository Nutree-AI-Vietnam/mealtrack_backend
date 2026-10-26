"""add engagement notification toggles

Revision ID: 20261026185557393061
Revises: 20261013115453616929
Create Date: 2026-10-26 18:55:57.414397

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261026185557393061"
down_revision: str | None = "20261013115453616929"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Engagement toggles. Offers stay off until the user opts in
    # (App Store guideline 4.5.4); the rest default to on.
    # Autogenerate also listed unrelated pre-existing model/schema drift;
    # it was removed so this revision only adds these columns.
    op.add_column(
        "notification_preferences",
        sa.Column(
            "streak_reminders_enabled",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
    )
    op.add_column(
        "notification_preferences",
        sa.Column(
            "reengagement_enabled", sa.Boolean(), server_default="true", nullable=False
        ),
    )
    op.add_column(
        "notification_preferences",
        sa.Column(
            "progress_updates_enabled",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
    )
    op.add_column(
        "notification_preferences",
        sa.Column(
            "offers_enabled", sa.Boolean(), server_default="false", nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("notification_preferences", "offers_enabled")
    op.drop_column("notification_preferences", "progress_updates_enabled")
    op.drop_column("notification_preferences", "reengagement_enabled")
    op.drop_column("notification_preferences", "streak_reminders_enabled")
