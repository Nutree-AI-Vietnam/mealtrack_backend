"""Restore the Alembic revision stamped on staging without a repo file.

Staging ``alembic_version`` is ``20260913160551428459``. That identifier was
applied (likely a local generate+upgrade against the staging DB) and the
script was later replaced by ``20260915035556051174`` without keeping the
old revision in git. Alembic cannot upgrade from an unknown current revision.

This revision is a graph bridge only: it does not change schema. The index
work lives in the following revision and is idempotent.

Revision ID: 20260913160551428459
Revises: 20260904044230151265
Create Date: 2026-09-13 16:05:51.428459
"""

from collections.abc import Sequence

revision: str = "20260913160551428459"
down_revision: str | None = "20260904044230151265"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Keep the orphaned staging stamp in the revision graph."""


def downgrade() -> None:
    """Return to the chat-tables head used before the orphaned stamp."""
