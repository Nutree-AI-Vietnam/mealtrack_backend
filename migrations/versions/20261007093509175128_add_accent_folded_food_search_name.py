"""add accent folded food search name

Revision ID: 20261007093509175128
Revises: 20261006115245714931
Create Date: 2026-10-07 16:35:09.179198

Adds ``food_reference.name_search``: the lowercase ASCII words of
"name_vi name" with Vietnamese and Latin diacritics folded, plus a trigram
index, so "pho bo" and "phở bò" both reach the same catalog rows. Adding a
stored generated column rewrites food_reference once (catalog rows only).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261007093509175128"
down_revision: str | None = "20261006115245714931"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRIGRAM_INDEX = "ix_food_reference_name_search_trgm"


def upgrade() -> None:
    # The expression is shared with the ORM model so Python query folding and
    # stored row folding cannot drift apart.
    from src.infra.database.food_reference_search_name_sql import (
        food_reference_search_name_postgresql_sql,
    )

    op.add_column(
        "food_reference",
        sa.Column(
            "name_search",
            sa.Text(),
            sa.Computed(food_reference_search_name_postgresql_sql(), persisted=True),
            nullable=True,
        ),
    )
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {_TRIGRAM_INDEX} "
        "ON food_reference USING gin (name_search gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_TRIGRAM_INDEX}")
    op.drop_column("food_reference", "name_search")
