"""Add nullable JSON reply_payload on chat_message for cards and chips."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903000001"
down_revision: str | None = "20260901000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_message",
        sa.Column("reply_payload", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_message", "reply_payload")
