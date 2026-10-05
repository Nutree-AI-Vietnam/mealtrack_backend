"""merge delivery unit widen and catalog preparation heads

Revision ID: 20261005022758771114
Revises: 20261004073003287646, 20261004125027631936
Create Date: 2026-10-05 09:27:58.788532

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20261005022758771114"
down_revision: Union[str, None] = ("20261004073003287646", "20261004125027631936")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
