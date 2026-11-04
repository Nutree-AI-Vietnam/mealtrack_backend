"""A person's supplement list."""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)

from src.infra.database.base import Base
from src.infra.database.models.base import BaseMixin


class UserSupplementORM(Base, BaseMixin):
    __tablename__ = "user_supplements"

    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id = Column(
        String(36),
        ForeignKey("supplement_products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    units_per_dose = Column(Float, nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("user_id", "product_id", name="uq_user_supplements_product"),
        CheckConstraint("units_per_dose > 0", name="ck_user_supplements_units"),
    )
