"""add supplement product image

Revision ID: 20261103044352264010
Revises: 20261102154908763403
Create Date: 2026-11-03 11:43:52.286681

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261103044352264010"
down_revision: Union[str, None] = "20261102154908763403"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "supplement_products",
        sa.Column("image_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "supplement_products",
        sa.Column("image_url", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("supplement_products", "image_url")
    op.drop_column("supplement_products", "image_id")
