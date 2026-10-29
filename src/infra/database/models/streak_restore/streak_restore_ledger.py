"""Streak restore ledger: pack credits, refunds and restored days in one table."""

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)

from src.infra.database.base import Base


class StreakRestoreLedgerORM(Base):
    """Balance = SUM(delta); restored days = restored_date of spend and free rows."""

    __tablename__ = "streak_restore_ledger"

    id = Column(String(36), primary_key=True)
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind = Column(String(16), nullable=False)
    delta = Column(Integer, nullable=False)
    product_id = Column(String(128), nullable=True)
    # Lower-case app_store / play_store / test_store, kept as data only.
    store = Column(String(32), nullable=True)
    store_transaction_id = Column(String(255), nullable=True)
    environment = Column(String(16), nullable=True)
    restored_date = Column(Date, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('purchase', 'refund', 'spend', 'free')",
            name="ck_streak_restore_ledger_kind",
        ),
        CheckConstraint(
            "(kind = 'purchase' AND delta > 0) OR (kind = 'refund' AND delta <= 0)"
            " OR (kind = 'spend' AND delta = -1) OR (kind = 'free' AND delta = 0)",
            name="ck_streak_restore_ledger_delta",
        ),
        CheckConstraint(
            "kind NOT IN ('purchase', 'refund') OR store_transaction_id IS NOT NULL",
            name="ck_streak_restore_ledger_transaction",
        ),
        CheckConstraint(
            "(kind IN ('spend', 'free')) = (restored_date IS NOT NULL)",
            name="ck_streak_restore_ledger_restored_date",
        ),
        Index(
            "uq_streak_restore_ledger_restored_day",
            "user_id",
            "restored_date",
            unique=True,
            postgresql_where=text("kind IN ('spend', 'free')"),
        ),
        Index(
            "uq_streak_restore_ledger_one_free",
            "user_id",
            unique=True,
            postgresql_where=text("kind = 'free'"),
        ),
        Index(
            "uq_streak_restore_ledger_transaction",
            "store_transaction_id",
            "kind",
            unique=True,
            postgresql_where=text("store_transaction_id IS NOT NULL"),
        ),
    )
