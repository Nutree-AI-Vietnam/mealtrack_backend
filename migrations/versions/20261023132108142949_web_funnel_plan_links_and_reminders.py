"""web funnel plan links and reminders

Revision ID: 20261023132108142949
Revises: 20261013115453616929
Create Date: 2026-10-23 13:21:08.163601

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '20261023132108142949'
down_revision: Union[str, None] = '20261013115453616929'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Plan-link and reminder state on web-funnel leads. All columns are nullable."""
    op.add_column('web_funnel_leads', sa.Column('locale', sa.String(length=8), nullable=True))
    op.add_column('web_funnel_leads', sa.Column('display_units', sa.String(length=16), nullable=True))
    op.add_column('web_funnel_leads', sa.Column('off_plan_moment', sa.String(length=16), nullable=True))
    op.add_column('web_funnel_leads', sa.Column('plan_link_token_hash', sa.String(length=64), nullable=True))
    op.add_column('web_funnel_leads', sa.Column('plan_link_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('web_funnel_leads', sa.Column('reminders_unsubscribed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint('uq_web_funnel_leads_plan_link_token_hash', 'web_funnel_leads', ['plan_link_token_hash'])


def downgrade() -> None:
    op.drop_constraint('uq_web_funnel_leads_plan_link_token_hash', 'web_funnel_leads', type_='unique')
    op.drop_column('web_funnel_leads', 'reminders_unsubscribed_at')
    op.drop_column('web_funnel_leads', 'plan_link_expires_at')
    op.drop_column('web_funnel_leads', 'plan_link_token_hash')
    op.drop_column('web_funnel_leads', 'off_plan_moment')
    op.drop_column('web_funnel_leads', 'display_units')
    op.drop_column('web_funnel_leads', 'locale')
