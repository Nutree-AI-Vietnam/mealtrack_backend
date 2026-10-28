"""merge reviewed scan feedback and user avatar heads

Revision ID: 20261028142924585579
Revises: 20261026102822211100, 20261027154254949604
Create Date: 2026-10-28 21:29:24.728268

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20261028142924585579'
down_revision: Union[str, None] = ('20261026102822211100', '20261027154254949604')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass