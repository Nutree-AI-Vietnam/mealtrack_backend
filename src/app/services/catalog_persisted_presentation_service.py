"""Catalog GET presentation reads prepared values or canonical authored text."""

from src.domain.constants.languages import normalize_language
from src.domain.model.translation_result import TranslationResult


class CatalogPersistedPresentationService:
    def __init__(self, uow_factory, text_translator=None):
        self.uow_factory = uow_factory
        self._text_translator = text_translator

    async def get_catalog_translations(self, meals, language):
        locale = normalize_language(language)
        async with self.uow_factory() as uow:
            return await uow.catalog_preparation.load_translations(
                [meal.id for meal in meals], locale=locale
            )

    async def get_grocery_translations(self, categories, language):
        locale = normalize_language(language)
        if locale == "en":
            return {}
        items = [item for category in categories for item in category.items]
        ingredient_ids = [item.ingredient_id for item in items]
        async with self.uow_factory() as uow:
            references = (
                await uow.food_references.get_display_projections(
                    ingredient_ids, language="en"
                )
                if locale == "vi"
                else {}
            )
            prepared = await uow.catalog_preparation.load_ingredient_translations(
                ingredient_ids, locale=locale
            )
        translated = {}
        for item in items:
            reference = references.get(item.ingredient_id) or {}
            name = str(reference.get("name_vi") or "").strip() or prepared.get(
                item.ingredient_id
            )
            if name:
                translated[item.name] = name
        return translated

    async def translate_texts(self, texts, *args):
        # Grocery rows with Vietnamese display names and no food reference have
        # no prepared English label. Delegate that presentation translate to the
        # shared text translator when one is configured.
        if self._text_translator is not None:
            return await self._text_translator.translate_texts(texts, *args)
        return TranslationResult.unavailable(
            texts, source_language="en", target_language=args[-1] if args else "en"
        )
