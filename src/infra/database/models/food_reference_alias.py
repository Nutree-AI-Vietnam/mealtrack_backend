"""Alternate names that resolve onto an existing food_reference row."""

from __future__ import annotations

import uuid

from sqlalchemy import Column, ForeignKey, Integer, String

from src.infra.database.base import Base


class FoodReferenceAliasORM(Base):
    __tablename__ = "food_reference_aliases"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    food_reference_id = Column(
        Integer, ForeignKey("food_reference.id", ondelete="CASCADE"), nullable=False
    )
    alias = Column(String(255), nullable=False)
    normalized_alias = Column(String(255), nullable=False, unique=True)
    language = Column(String(16), nullable=True)
