"""Lookup and upsert exact FatSecret serving-phrase translations."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.services.serving_label import serving_phrase_key
from src.infra.database.models.serving_phrase_translation import (
    ServingPhraseTranslationModel,
)


class ServingPhraseRepository:
    """Shared phrase cache keyed by folded English text + language."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_translations(
        self, phrases: list[str], language: str
    ) -> dict[str, str]:
        keys = [serving_phrase_key(phrase) for phrase in phrases if phrase.strip()]
        if not keys or not language or language == "en":
            return {}
        result = await self._session.execute(
            select(
                ServingPhraseTranslationModel.source_key,
                ServingPhraseTranslationModel.translated_text,
            ).where(
                ServingPhraseTranslationModel.language == language,
                ServingPhraseTranslationModel.source_key.in_(keys),
            )
        )
        return {row.source_key: row.translated_text for row in result.all()}

    async def upsert_translations(
        self, labels_by_source: dict[str, str], language: str
    ) -> None:
        if not labels_by_source or not language or language == "en":
            return
        rows = [
            {
                "source_key": serving_phrase_key(source),
                "language": language,
                "translated_text": translated[:100],
            }
            for source, translated in labels_by_source.items()
            if serving_phrase_key(source) and translated.strip()
        ]
        if not rows:
            return
        stmt = insert(ServingPhraseTranslationModel).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_serving_phrase_translation_source_language",
            set_={"translated_text": stmt.excluded.translated_text},
        )
        await self._session.execute(stmt)
