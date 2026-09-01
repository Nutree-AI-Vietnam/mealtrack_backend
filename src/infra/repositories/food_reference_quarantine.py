"""Shared SQL clause that keeps AI estimates out of canonical discovery."""

from sqlalchemy import and_, or_
from sqlalchemy.sql.elements import ColumnElement

from src.infra.database.models.food_reference_model import FoodReferenceModel

AI_ESTIMATE_SOURCE = "ai_estimate"


def ai_estimate_quarantine_clause() -> ColumnElement[bool]:
    """Exclude estimate rows from public catalog / search queries.

    Matches both ``source_namespace`` and legacy ``source`` so older estimate
    rows cannot leak into name search, locale lookup, or catalog seeding.
    """
    return and_(
        or_(
            FoodReferenceModel.source_namespace != AI_ESTIMATE_SOURCE,
            FoodReferenceModel.source_namespace.is_(None),
        ),
        FoodReferenceModel.source != AI_ESTIMATE_SOURCE,
    )
