"""Food-reference adapter backed by fresh async Unit of Work scopes."""

from __future__ import annotations

from typing import Any

from src.infra.database.uow_async import AsyncUnitOfWork


class AsyncFoodReferenceUowAdapter:
    """Expose food-reference repo methods without storing an AsyncSession."""

    def __init__(self, uow_factory: Any = AsyncUnitOfWork):
        self._uow_factory = uow_factory

    async def get_by_barcode(self, barcode: str) -> dict[str, Any] | None:
        async with self._uow_factory() as uow:
            return await uow.food_references.get_by_barcode(barcode)

    async def get_by_id(self, ref_id: int) -> dict[str, Any] | None:
        async with self._uow_factory() as uow:
            return await uow.food_references.get_by_id(ref_id)

    async def get_by_fdc_id(self, fdc_id: int) -> dict[str, Any] | None:
        async with self._uow_factory() as uow:
            return await uow.food_references.get_by_fdc_id(fdc_id)

    async def search_by_name(
        self, query: str, region: str = "global", limit: int = 10
    ) -> list[dict[str, Any]]:
        async with self._uow_factory() as uow:
            return await uow.food_references.search_by_name(query, region, limit)

    async def find_batch_by_normalized_names(
        self, names_normalized: list[str]
    ) -> dict[str, dict[str, Any]]:
        async with self._uow_factory() as uow:
            return await uow.food_references.find_batch_by_normalized_names(
                names_normalized
            )

    async def find_by_normalized_name(
        self, name_normalized: str
    ) -> dict[str, Any] | None:
        async with self._uow_factory() as uow:
            return await uow.food_references.find_by_normalized_name(name_normalized)

    async def upsert_by_normalized_name(
        self,
        name: str,
        name_normalized: str,
        protein_100g: float,
        carbs_100g: float,
        fat_100g: float,
        fiber_100g: float,
        sugar_100g: float,
        source: str,
        is_verified: bool,
        external_id: str | None = None,
        source_namespace: str | None = None,
    ) -> dict[str, Any] | None:
        async with self._uow_factory() as uow:
            return await uow.food_references.upsert_by_normalized_name(
                name=name,
                name_normalized=name_normalized,
                protein_100g=protein_100g,
                carbs_100g=carbs_100g,
                fat_100g=fat_100g,
                fiber_100g=fiber_100g,
                sugar_100g=sugar_100g,
                source=source,
                is_verified=is_verified,
                external_id=external_id,
                source_namespace=source_namespace,
            )

    async def upsert(self, data: dict[str, Any]) -> None:
        async with self._uow_factory() as uow:
            await uow.food_references.upsert(data)

    async def get_nutrition_projection(self, food_reference_id: int) -> Any | None:
        async with self._uow_factory() as uow:
            return await uow.food_references.get_nutrition_projection(food_reference_id)

    async def find_by_source_identity(
        self, namespace: str, food_id: str
    ) -> dict[str, Any] | None:
        async with self._uow_factory() as uow:
            return await uow.food_references.find_by_source_identity(
                namespace, food_id
            )

    async def adopt_provider_food(
        self,
        namespace: str,
        food_id: str,
        english_name: str,
        per_100g: dict[str, Any],
        servings: list[dict[str, Any]] | None,
        locale: str,
        locale_name: str,
    ) -> dict[str, Any]:
        async with self._uow_factory() as uow:
            return await uow.food_references.adopt_provider_food(
                namespace,
                food_id,
                english_name,
                per_100g,
                servings,
                locale,
                locale_name,
            )

    async def find_by_locale_names(
        self, language: str, names: list[str]
    ) -> dict[str, dict[str, Any]]:
        async with self._uow_factory() as uow:
            return await uow.food_references.find_by_locale_names(language, names)

    async def get_display_projections(
        self, food_reference_ids: list[int], language: str | None = None
    ) -> dict[int, dict[str, Any]]:
        async with self._uow_factory() as uow:
            return await uow.food_references.get_display_projections(
                food_reference_ids, language
            )

    async def get_serving_phrase_translations(
        self, phrases: list[str], language: str
    ) -> dict[str, str]:
        async with self._uow_factory() as uow:
            return await uow.food_references.get_serving_phrase_translations(
                phrases, language
            )

    async def upsert_serving_phrase_translations(
        self, labels_by_source: dict[str, str], language: str
    ) -> None:
        async with self._uow_factory() as uow:
            await uow.food_references.upsert_serving_phrase_translations(
                labels_by_source, language
            )

    async def apply_serving_name_vi(
        self, food_reference_id: int, labels_by_unit: dict[str, str]
    ) -> None:
        async with self._uow_factory() as uow:
            await uow.food_references.apply_serving_name_vi(
                food_reference_id, labels_by_unit
            )
