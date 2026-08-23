"""Cached exact translations of FatSecret serving phrases."""

from sqlalchemy import Column, Integer, String, UniqueConstraint

from src.infra.database.base import Base


class ServingPhraseTranslationModel(Base):
    """Reuse one FatSecret phrase translation across foods."""

    __tablename__ = "serving_phrase_translation"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_key = Column(String(120), nullable=False)
    language = Column(String(8), nullable=False)
    translated_text = Column(String(100), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "source_key",
            "language",
            name="uq_serving_phrase_translation_source_language",
        ),
    )
