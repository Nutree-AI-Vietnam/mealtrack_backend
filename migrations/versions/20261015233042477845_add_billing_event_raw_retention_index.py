"""Add billing event raw retention index (NM-680, D9).

Partial index on ``billing_provider_events.received_at`` for rows that still
hold a stored webhook body, so the entitlement worker can null bodies older
than 90 days in small batches. Mirrors ``ix_bpe_raw_retention`` on the ORM.

Autogenerate also reported unrelated pre-existing model/schema drift; only
this index is kept here.

Revision ID: 20261015233042477845
Revises: 20261015184814225029
Create Date: 2026-10-15 23:30:42.536139

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261015233042477845"
down_revision: str | None = "20261015184814225029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_bpe_raw_retention",
        "billing_provider_events",
        ["received_at"],
        unique=False,
        postgresql_where=sa.text("raw IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bpe_raw_retention",
        table_name="billing_provider_events",
        postgresql_where=sa.text("raw IS NOT NULL"),
    )
