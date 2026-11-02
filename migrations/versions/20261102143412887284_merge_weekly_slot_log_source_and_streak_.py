"""merge weekly slot log source and streak log day heads

Revision ID: 20261102143412887284
Revises: 20261102111619991686, 20261102140358147160
Create Date: 2026-11-02 14:34:12.954519

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20261102143412887284"
down_revision: Union[str, None] = ("20261102111619991686", "20261102140358147160")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
