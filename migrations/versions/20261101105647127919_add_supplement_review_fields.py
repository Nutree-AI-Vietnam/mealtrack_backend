"""add supplement review fields

Revision ID: 20261101105647127919
Revises: 20261101085347607226
Create Date: 2026-11-01 17:56:47.127919

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20261101105647127919"
down_revision: Union[str, None] = "20261101085347607226"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "supplement_products",
        sa.Column(
            "review_reasons",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "supplement_products",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "supplement_products",
        sa.Column("reviewed_by", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("supplement_products", "reviewed_by")
    op.drop_column("supplement_products", "reviewed_at")
    op.drop_column("supplement_products", "review_reasons")
