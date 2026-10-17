"""allow multiple chat threads per user

Revision ID: 20261015014451225604
Revises: 20261013020801296576
Create Date: 2026-10-15 01:44:51.260202

Drops the one-thread-per-user unique index and indexes threads by recent
activity for the thread list.

Rollout: deploy the multi-thread server first, then run this migration. The
single-thread server inserts with ON CONFLICT (user_id), which fails once the
unique index is gone; the multi-thread server works with or without it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261015014451225604"
down_revision: str | None = "20261013020801296576"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_chat_thread_user_updated",
        "chat_thread",
        ["user_id", "updated_at"],
        unique=False,
    )
    op.execute("DROP INDEX IF EXISTS ix_chat_thread_user_id")
    # A schema built from the models (create_all + stamp) also carries the
    # column-level unique constraint.
    op.execute(
        "ALTER TABLE chat_thread DROP CONSTRAINT IF EXISTS chat_thread_user_id_key"
    )


def downgrade() -> None:
    # Restoring one thread per user would mean deleting chat history, so stop
    # instead and let an operator decide which threads to keep.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM chat_thread GROUP BY user_id HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'users have more than one chat thread; merge or delete '
                    'extra threads before downgrading';
            END IF;
        END $$;
        """
    )
    op.create_index("ix_chat_thread_user_id", "chat_thread", ["user_id"], unique=True)
    op.drop_index("ix_chat_thread_user_updated", table_name="chat_thread")
