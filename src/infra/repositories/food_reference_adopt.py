"""Identity-scoped provider adopt writes and Vietnamese name updates."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value

from src.domain.constants.languages import normalize_language
from src.domain.services.food_reference_identity import (
    platform_name_normalized,
    sanitize_locale_name,
)
from src.domain.services.nutrition_integrity_policy import NutritionIntegrityPolicy
from src.infra.database.models.food_reference_model import FoodReferenceModel
from src.infra.repositories.food_reference_integrity_repository import (
    FoodReferenceIntegrityRepository,
)
from src.infra.repositories.food_reference_projection import (
    build_food_reference_serving_rows,
    food_reference_model_to_dict,
)

logger = logging.getLogger(__name__)

_ADOPT_LOAD_OPTIONS = (
    selectinload(FoodReferenceModel.serving_size_rows),
    selectinload(FoodReferenceModel.nutrient_rows),
)
_CHILD_COLLECTIONS = ("serving_size_rows", "nutrient_rows")


def _prime_empty_collections(model: FoodReferenceModel) -> None:
    for attr in _CHILD_COLLECTIONS:
        if attr in inspect(model).unloaded:
            set_committed_value(model, attr, [])


def _loaded_collection(model: FoodReferenceModel, attr: str) -> Any:
    if attr in inspect(model).unloaded:
        return None
    return getattr(model, attr)


def _replace_relationship_collection(
    model: FoodReferenceModel, attr: str, rows: list[Any]
) -> None:
    # Assignment would otherwise lazy-load the old collection and raise
    # MissingGreenlet on AsyncSession.
    if attr in inspect(model).unloaded:
        set_committed_value(model, attr, [])
    setattr(model, attr, rows)


class FoodReferenceAdoptRepository:
    """Own the identity-first adopt write path and ``name_vi`` updates."""

    def __init__(
        self,
        session: AsyncSession,
        integrity_policy: NutritionIntegrityPolicy | None = None,
    ):
        self._session = session
        self._integrity_policy = integrity_policy or NutritionIntegrityPolicy()
        self._integrity_repository = FoodReferenceIntegrityRepository(session)

    async def find_by_source_identity(
        self, namespace: str, food_id: str
    ) -> dict[str, Any] | None:
        model = await self._find_model_by_source_identity(namespace, food_id)
        return food_reference_model_to_dict(model) if model else None

    async def adopt_provider_food(
        self,
        namespace: str,
        food_id: str,
        english_name: str,
        per_100g: Mapping[str, Any],
        servings: list[dict[str, Any]] | None,
        locale: str,
        locale_name: str,
    ) -> dict[str, Any]:
        if not namespace or not food_id:
            raise ValueError("adopt_provider_food requires namespace and food_id")
        model = await self._find_or_insert_identity_row(namespace, str(food_id))
        await self._load_child_collections(model)
        self._apply_nutrition(model, english_name, per_100g, servings)
        await self._session.flush()
        clean_locale_name = sanitize_locale_name(locale_name)
        self._apply_name_vi(model, locale, clean_locale_name)
        await self._integrity_repository.materialize_reference(
            model, actor_kind="system", reason_code="provider_adopt"
        )
        await self._session.flush()
        return {
            **food_reference_model_to_dict(model),
            "name_normalized": model.name_normalized,
            "locale": locale,
            "locale_name": clean_locale_name or None,
        }

    def _apply_name_vi(
        self, model: FoodReferenceModel, locale: str, locale_name: str
    ) -> None:
        if normalize_language(locale) != "vi" or not locale_name:
            return
        model.name_vi = locale_name

    async def _find_or_insert_identity_row(
        self, namespace: str, food_id: str
    ) -> FoodReferenceModel:
        existing = await self._find_model_by_source_identity(namespace, food_id)
        if existing is not None:
            return existing
        identity_key = platform_name_normalized(namespace, food_id)
        new_model = FoodReferenceModel(
            name=identity_key,
            name_normalized=identity_key,
            source=namespace,
            source_namespace=namespace,
            source_food_id=food_id,
            is_verified=False,
            density=1.0,
            fiber_100g=0,
            sugar_100g=0,
        )
        try:
            # Savepoint: a concurrent write on the same identity rolls back
            # only this insert, not the outer UoW.
            async with self._session.begin_nested():
                self._session.add(new_model)
                await self._session.flush()
        except IntegrityError:
            logger.info(
                "food_reference_adopt.identity_insert_raced %s:%s", namespace, food_id
            )
        else:
            _prime_empty_collections(new_model)
            return new_model
        reread = await self._find_model_by_source_identity(namespace, food_id)
        if reread is not None:
            return reread
        raise RuntimeError(
            f"food reference adopt failed to materialize identity {namespace}:{food_id}"
        )

    def _apply_nutrition(
        self,
        model: FoodReferenceModel,
        english_name: str,
        per_100g: Mapping[str, Any],
        servings: list[dict[str, Any]] | None,
    ) -> None:
        clean_name = str(english_name or "").strip()
        if model.is_verified:
            # Verified density is frozen; only add missing catalog metadata.
            if clean_name and not str(model.name or "").strip():
                model.name = clean_name
            return
        macros = {
            "protein_100g": per_100g.get("protein_100g"),
            "carbs_100g": per_100g.get("carbs_100g"),
            "fat_100g": per_100g.get("fat_100g"),
            "fiber_100g": per_100g.get("fiber_100g", 0),
            "sugar_100g": per_100g.get("sugar_100g", 0),
        }
        self._integrity_policy.require_valid(
            {**macros, "allowed_units": servings},
            require_energy=False,
            require_metric_basis=False,
        )
        if clean_name:
            model.name = clean_name
        for field, value in macros.items():
            setattr(model, field, value)
        existing_servings = _loaded_collection(model, "serving_size_rows")
        if servings:
            _replace_relationship_collection(
                model,
                "serving_size_rows",
                build_food_reference_serving_rows(servings),
            )
        elif existing_servings is not None and not existing_servings:
            _replace_relationship_collection(
                model,
                "serving_size_rows",
                build_food_reference_serving_rows([{"name": "g", "grams": 1.0}]),
            )
        model.is_verified = True

    async def _load_child_collections(self, model: FoodReferenceModel) -> None:
        if "serving_size_rows" in inspect(model).unloaded:
            await model.awaitable_attrs.serving_size_rows
        if "nutrient_rows" in inspect(model).unloaded:
            await model.awaitable_attrs.nutrient_rows

    async def _find_model_by_source_identity(
        self, namespace: str, food_id: str
    ) -> FoodReferenceModel | None:
        if not namespace or not food_id:
            return None
        stmt = select(FoodReferenceModel).where(
            FoodReferenceModel.source_namespace == namespace,
            FoodReferenceModel.source_food_id == str(food_id),
        )
        result = await self._session.execute(stmt.options(*_ADOPT_LOAD_OPTIONS))
        return result.scalars().first()


__all__ = ["FoodReferenceAdoptRepository", "sanitize_locale_name"]
