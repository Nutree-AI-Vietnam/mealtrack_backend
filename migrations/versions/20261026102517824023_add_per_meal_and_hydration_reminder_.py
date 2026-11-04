"""add per meal and hydration reminder prefs

Revision ID: 20261026102517824023
Revises: 20261013115453616929
Create Date: 2026-10-26 17:25:17.858006

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261026102517824023"
down_revision: str | None = "20261013115453616929"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MEAL_SWITCHES = (
    "breakfast_reminder_enabled",
    "lunch_reminder_enabled",
    "dinner_reminder_enabled",
)


def upgrade() -> None:
    for column_name in _MEAL_SWITCHES:
        op.add_column(
            "notification_preferences",
            sa.Column(column_name, sa.Boolean(), server_default="true", nullable=False),
        )
    op.add_column(
        "notification_preferences",
        sa.Column(
            "hydration_reminder_time_minutes",
            sa.Integer(),
            server_default="930",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "check_hydration_reminder_time",
        "notification_preferences",
        "hydration_reminder_time_minutes >= 0 "
        "AND hydration_reminder_time_minutes < 1440",
    )
    # Each meal starts from the single switch older apps set for all three.
    op.execute(
        "UPDATE notification_preferences SET "
        "breakfast_reminder_enabled = meal_reminders_enabled, "
        "lunch_reminder_enabled = meal_reminders_enabled, "
        "dinner_reminder_enabled = meal_reminders_enabled"
    )


def downgrade() -> None:
    op.drop_constraint(
        "check_hydration_reminder_time", "notification_preferences", type_="check"
    )
    op.drop_column("notification_preferences", "hydration_reminder_time_minutes")
    for column_name in reversed(_MEAL_SWITCHES):
        op.drop_column("notification_preferences", column_name)
