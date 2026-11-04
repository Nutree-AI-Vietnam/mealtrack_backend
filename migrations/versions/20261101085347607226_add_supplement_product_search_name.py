"""add supplement product search name

Revision ID: 20261101085347607226
Revises: 20261101075236637267
Create Date: 2026-11-01 15:53:47.621013

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261101085347607226"
down_revision: Union[str, None] = "20261101075236637267"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TRIGRAM_INDEX = "ix_supplement_products_name_search_trgm"


def upgrade() -> None:
    # The expression is shared with the ORM model so query folding and stored
    # row folding cannot drift apart (as for food_reference.name_search).
    from src.infra.database.supplement_search_name_sql import (
        supplement_search_name_postgresql_sql,
    )

    op.add_column(
        "supplement_products",
        sa.Column(
            "name_search",
            sa.Text(),
            sa.Computed(supplement_search_name_postgresql_sql(), persisted=True),
            nullable=True,
        ),
    )
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        _TRIGRAM_INDEX,
        "supplement_products",
        ["name_search"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"name_search": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index(_TRIGRAM_INDEX, table_name="supplement_products")
    op.drop_column("supplement_products", "name_search")
