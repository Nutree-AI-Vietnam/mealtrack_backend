"""merge recipe import job type and weekly slot heads

Revision ID: 20261102150440068281
Revises: 20261101091406080591, 20261102143412887284
Create Date: 2026-11-02 15:04:40.112242

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20261102150440068281"
down_revision: Union[str, None] = ("20261101091406080591", "20261102143412887284")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
