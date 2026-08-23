"""Async food reference repository."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.domain.ports.food_reference_repository_port import (
    FoodReferenceNutritionProjection,
    FoodReferenceSearchProjection,
)
from src.domain.services.food_reference_identity import (
    FATSECRET_NAMESPACE,
    platform_identity_prefix,
    platform_name_normalized,
)
from src.domain.services.meal_suggestion.ingredient_name_normalizer import (
    normalize_food_name,
)
from src.domain.services.nutrition_integrity_policy import NutritionIntegrityPolicy
from src.infra.database.models.food_reference_model import FoodReferenceModel
from src.infra.repositories.food_reference_adopt import (
    FoodReferenceAdoptRepository,
    _loaded_collection,
    _replace_relationship_collection,
)
from src.infra.repositories.food_reference_integrity_repository import (
    FoodReferenceIntegrityRepository,
)
from src.infra.repositories.food_reference_locale import FoodReferenceLocaleRepository
from src.infra.repositories.food_reference_projection import (
    FOOD_REFERENCE_SEED_COLUMNS,
    build_food_reference_nutrient_rows,
    build_food_reference_serving_rows,
    food_reference_model_to_dict,
    food_reference_model_to_integrity_data,
    food_reference_model_to_nutrition_projection,
)

logger = logging.getLogger(__name__)

_FOOD_REFERENCE_LOAD_OPTIONS = (
    selectinload(FoodReferenceModel.serving_size_rows),
    selectinload(FoodReferenceModel.nutrient_rows),
)


class AsyncFoodReferenceRepository:
    """Async repository for food reference CRUD operations."""

    _SEED_COLUMNS = FOOD_REFERENCE_SEED_COLUMNS

    def __init__(
        self,
        session: AsyncSession,
        integrity_policy: NutritionIntegrityPolicy | None = None,
    ):
        self._session = session
        self._integrity_policy = integrity_policy or NutritionIntegrityPolicy()
        self._integrity_repository = FoodReferenceIntegrityRepository(session)
        self._adopt_repository = FoodReferenceAdoptRepository(
            session, self._integrity_policy
        )
        self._locale_repository = FoodReferenceLocaleRepository(session)

    async def get_by_barcode(self, barcode: str) -> dict[str, Any] | None:
        stmt = (
            select(FoodReferenceModel)
            .where(FoodReferenceModel.barcode == barcode)
            .where(self._integrity_repository.public_eligibility_clause())
            .options(*_FOOD_REFERENCE_LOAD_OPTIONS)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return food_reference_model_to_dict(model) if model else None

    async def get_by_id(self, ref_id: int) -> dict[str, Any] | None:
        stmt = (
            select(FoodReferenceModel)
            .where(FoodReferenceModel.id == ref_id)
            .where(self._integrity_repository.public_eligibility_clause())
            .options(*_FOOD_REFERENCE_LOAD_OPTIONS)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return food_reference_model_to_dict(model) if model else None

    async def get_nutrition_projection(
        self,
        food_reference_id: int,
    ) -> FoodReferenceNutritionProjection | None:
        stmt = (
            select(FoodReferenceModel)
            .where(FoodReferenceModel.id == food_reference_id)
            .where(self._integrity_repository.public_eligibility_clause())
            .options(*_FOOD_REFERENCE_LOAD_OPTIONS)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return food_reference_model_to_nutrition_projection(model) if model else None

    async def get_nutrition_projections(
        self, food_reference_ids: list[int], *, for_update: bool = False
    ) -> dict[int, FoodReferenceNutritionProjection]:
        ids = sorted({int(value) for value in food_reference_ids})
        if not ids:
            return {}
        statement = (
            select(FoodReferenceModel)
            .where(FoodReferenceModel.id.in_(ids))
            .where(self._integrity_repository.public_eligibility_clause())
            .options(*_FOOD_REFERENCE_LOAD_OPTIONS)
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return {
            model.id: food_reference_model_to_nutrition_projection(model)
            for model in result.scalars().all()
        }

    async def list_catalog_seed_candidates(
        self,
    ) -> list[FoodReferenceNutritionProjection]:
        result = await self._session.execute(
            select(
                FoodReferenceModel.id,
                FoodReferenceModel.name,
                FoodReferenceModel.name_normalized,
                FoodReferenceModel.source,
                FoodReferenceModel.source_namespace,
                FoodReferenceModel.source_food_id,
                FoodReferenceModel.is_verified,
                FoodReferenceModel.protein_100g,
                FoodReferenceModel.carbs_100g,
                FoodReferenceModel.fat_100g,
                FoodReferenceModel.fiber_100g,
                FoodReferenceModel.sugar_100g,
                FoodReferenceModel.density,
            )
            .where(self._integrity_repository.public_eligibility_clause())
            .order_by(
                FoodReferenceModel.is_verified.desc(),
                FoodReferenceModel.id.asc(),
            )
        )
        return [
            projection
            for row in result.all()
            if (projection := _catalog_seed_candidate_projection(row))
            and _projection_is_integrity_valid(projection, self._integrity_policy)
        ]

    async def find_catalog_seed_candidates_by_normalized_name(
        self,
        name_normalized: str,
    ) -> list[FoodReferenceNutritionProjection]:
        result = await self._session.execute(
            select(
                FoodReferenceModel.id,
                FoodReferenceModel.name,
                FoodReferenceModel.name_normalized,
                FoodReferenceModel.source,
                FoodReferenceModel.source_namespace,
                FoodReferenceModel.source_food_id,
                FoodReferenceModel.is_verified,
                FoodReferenceModel.protein_100g,
                FoodReferenceModel.carbs_100g,
                FoodReferenceModel.fat_100g,
                FoodReferenceModel.fiber_100g,
                FoodReferenceModel.sugar_100g,
                FoodReferenceModel.density,
            )
            .where(FoodReferenceModel.name_normalized == name_normalized)
            .where(self._integrity_repository.public_eligibility_clause())
            .order_by(
                FoodReferenceModel.is_verified.desc(), FoodReferenceModel.id.asc()
            )
        )
        return [
            projection
            for row in result.all()
            if (projection := _catalog_seed_candidate_projection(row))
            and _projection_is_integrity_valid(projection, self._integrity_policy)
        ]

    async def approve_for_catalog_seed(
        self,
        food_reference_id: int,
    ) -> FoodReferenceNutritionProjection | None:
        """Persist an administrator's review decision for catalog publication."""

        result = await self._session.execute(
            select(FoodReferenceModel)
            .where(FoodReferenceModel.id == food_reference_id)
            .options(*_FOOD_REFERENCE_LOAD_OPTIONS)
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        self._integrity_policy.require_valid(
            food_reference_model_to_integrity_data(model),
            require_energy=False,
            require_metric_basis=False,
        )
        model.is_verified = True
        await self._sync_normalized_children(model, {})
        await self._integrity_repository.materialize_reference(
            model,
            actor_kind="reviewer",
            reason_code="catalog_approval",
        )
        await self._session.flush()
        return food_reference_model_to_nutrition_projection(model)

    async def get_by_fdc_id(self, fdc_id: int) -> dict[str, Any] | None:
        stmt = (
            select(FoodReferenceModel)
            .where(FoodReferenceModel.fdc_id == fdc_id)
            .where(self._integrity_repository.public_eligibility_clause())
            .options(*_FOOD_REFERENCE_LOAD_OPTIONS)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return food_reference_model_to_dict(model) if model else None

    async def get_by_fdc_ids(self, fdc_ids: list[int]) -> dict[int, dict[str, Any]]:
        ids = sorted({int(value) for value in fdc_ids})
        if not ids:
            return {}
        result = await self._session.execute(
            select(FoodReferenceModel)
            .where(FoodReferenceModel.fdc_id.in_(ids))
            .where(self._integrity_repository.public_eligibility_clause())
            .options(*_FOOD_REFERENCE_LOAD_OPTIONS)
        )
        return {
            model.fdc_id: food_reference_model_to_dict(model)
            for model in result.scalars().all()
            if model.fdc_id is not None
        }

    async def search_by_name(
        self, query: str, region: str = "global", limit: int = 10
    ) -> list[dict[str, Any]]:
        stmt = (
            select(FoodReferenceModel)
            .where(FoodReferenceModel.name.ilike(f"%{query}%"))
            .where(FoodReferenceModel.region.in_([region, "global"]))
            .where(self._integrity_repository.public_eligibility_clause())
            .options(*_FOOD_REFERENCE_LOAD_OPTIONS)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [food_reference_model_to_dict(row) for row in result.scalars().all()]

    async def search_local(
        self,
        query: str,
        region: str,
        limit: int,
    ) -> list[FoodReferenceSearchProjection]:
        raw_query = str(query or "").strip()
        identity_query = (
            raw_query.lower()
            if raw_query.lower().startswith(f"{FATSECRET_NAMESPACE}:")
            else None
        )
        normalized_query = normalize_food_name(query)
        if not normalized_query and not identity_query:
            return []

        bounded_limit = min(max(limit, 1), 50)
        like = f"%{normalized_query}%" if normalized_query else None
        identity_prefix = f"{platform_identity_prefix(FATSECRET_NAMESPACE)}%"
        display_match = (
            or_(
                FoodReferenceModel.name.ilike(like),
                FoodReferenceModel.name_vi.ilike(like),
            )
            if like
            else None
        )
        if identity_query:
            key_match = func.lower(FoodReferenceModel.name_normalized) == identity_query
            match_clause = (
                or_(display_match, key_match) if display_match is not None else key_match
            )
            similarity_score = func.similarity(
                FoodReferenceModel.name_normalized, identity_query
            )
        else:
            key_match = and_(
                FoodReferenceModel.name_normalized.ilike(like),
                FoodReferenceModel.name_normalized.notlike(identity_prefix),
            )
            match_clause = (
                or_(display_match, key_match) if display_match is not None else key_match
            )
            similarity_score = func.similarity(
                FoodReferenceModel.name, normalized_query
            )
        base_stmt = (
            select(FoodReferenceModel)
            .where(self._integrity_repository.public_eligibility_clause())
            .where(FoodReferenceModel.region.in_([region, "global"]))
            .where(match_clause)
            .options(*_FOOD_REFERENCE_LOAD_OPTIONS)
            .order_by(
                FoodReferenceModel.is_verified.desc(),
                similarity_score.desc(),
                FoodReferenceModel.id.asc(),
            )
        )
        fetch_size = max(bounded_limit * 3, 10)
        offset = 0
        projections: list[FoodReferenceSearchProjection] = []
        seen: set[str] = set()
        while True:
            result = await self._session.execute(
                base_stmt.limit(fetch_size).offset(offset)
            )
            batch = result.scalars().all()
            projections.extend(
                _dedupe_search_projections(
                    batch,
                    bounded_limit - len(projections),
                    integrity_policy=self._integrity_policy,
                    seen=seen,
                )
            )
            if len(projections) >= bounded_limit:
                break
            offset += len(batch)
            if len(batch) < fetch_size:
                break
        return projections

    async def find_by_source_identity(
        self, namespace: str, food_id: str
    ) -> dict[str, Any] | None:
        return await self._adopt_repository.find_by_source_identity(namespace, food_id)

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
        return await self._adopt_repository.adopt_provider_food(
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
        return await self._locale_repository.find_by_locale_names(language, names)

    async def get_display_projections(
        self, food_reference_ids: list[int]
    ) -> dict[int, dict[str, Any]]:
        return await self._locale_repository.get_display_projections(food_reference_ids)

    async def upsert(self, data: dict[str, Any]) -> None:
        """Insert or update a food reference by barcode without owning commit."""
        source_namespace, source_food_id = _source_identity_from_data(data)
        identity_name_normalized = None
        if source_namespace and source_food_id:
            existing = (
                await self._find_model_by_barcode(data["barcode"])
                if data.get("barcode")
                else None
            )
            existing_key = existing.name_normalized if existing is not None else None
            if existing_key:
                await self._ensure_source_identity_collision_safe(
                    existing_key, source_namespace, source_food_id
                )
            else:
                identity_name_normalized = platform_name_normalized(
                    source_namespace, source_food_id
                )
        if data.get("is_verified", False):
            self._integrity_policy.require_valid(
                data,
                require_energy=False,
                require_metric_basis=False,
            )
        if data.get("barcode") and not data.get("is_verified", False):
            existing = await self._find_model_by_barcode(data["barcode"])
            if existing is not None and existing.is_verified:
                return

        values = {
            "barcode": data.get("barcode"),
            "name": data.get("name"),
            "name_normalized": identity_name_normalized,
            "name_vi": data.get("name_vi"),
            "brand": data.get("brand"),
            "protein_100g": data.get("protein_100g"),
            "carbs_100g": data.get("carbs_100g"),
            "fat_100g": data.get("fat_100g"),
            "fiber_100g": data.get("fiber_100g", 0),
            "sugar_100g": data.get("sugar_100g", 0),
            "serving_size": data.get("serving_size"),
            "serving_sizes": data.get("serving_sizes") or data.get("allowed_units"),
            "image_url": data.get("image_url"),
            "source": data.get("source", "fatsecret"),
            "is_verified": data.get("is_verified", False),
            "fdc_id": data.get("fdc_id"),
            "source_namespace": data.get("source_namespace"),
            "source_food_id": data.get("source_food_id"),
            "category": data.get("category"),
            "region": data.get("region", "global"),
            "density": data.get("density", 1.0),
            "extra_nutrients": data.get("extra_nutrients"),
        }
        stmt = pg_insert(FoodReferenceModel).values(**values)
        update_fields = {k: v for k, v in values.items() if k != "barcode"}
        if identity_name_normalized is None:
            update_fields.pop("name_normalized", None)
        if values["source_namespace"] is None or values["source_food_id"] is None:
            update_fields.pop("source_namespace", None)
            update_fields.pop("source_food_id", None)
        if not values["is_verified"]:
            update_fields.pop("is_verified", None)
        on_conflict_kwargs: dict[str, Any] = {
            "index_elements": [FoodReferenceModel.barcode],
            "set_": update_fields,
        }
        if not values["is_verified"]:
            on_conflict_kwargs["where"] = FoodReferenceModel.is_verified.is_(False)
        await self._session.execute(stmt.on_conflict_do_update(**on_conflict_kwargs))
        await self._session.flush()

        refreshed = await self._find_after_upsert(values)
        if refreshed:
            await self._sync_normalized_children(refreshed, data)
        if isinstance(refreshed, FoodReferenceModel):
            await self._integrity_repository.materialize_reference(refreshed)

    async def upsert_seed(self, data: dict[str, Any]) -> None:
        """Upsert a canonical, non-barcoded seed without owning commit."""

        name = str(data.get("name") or data.get("name_vi") or "").strip()
        if not name:
            raise ValueError("seed food requires a name")
        name_normalized = str(
            data.get("name_normalized") or normalize_food_name(name)
        ).strip()
        if not name_normalized:
            raise ValueError("seed food requires a normalized name")
        source_namespace, source_food_id = _source_identity_from_data(data)
        if source_namespace and source_food_id:
            await self._ensure_source_identity_collision_safe(
                name_normalized, source_namespace, source_food_id
            )
        if data.get("is_verified", False):
            self._integrity_policy.require_valid(
                data,
                require_energy=False,
                require_metric_basis=False,
            )

        values = {
            "name": name,
            "name_normalized": name_normalized,
            "name_vi": data.get("name_vi"),
            "brand": data.get("brand"),
            "category": _truncate_category(data.get("category")),
            "region": data.get("region", "VN"),
            "protein_100g": data.get("protein_100g"),
            "carbs_100g": data.get("carbs_100g"),
            "fat_100g": data.get("fat_100g"),
            "fiber_100g": data.get("fiber_100g", 0),
            "sugar_100g": data.get("sugar_100g", 0),
            "serving_size": data.get("serving_size"),
            "serving_sizes": data.get("serving_sizes") or data.get("allowed_units"),
            "image_url": data.get("image_url"),
            "source": data.get("source", "seed"),
            "source_namespace": data.get("source_namespace"),
            "source_food_id": data.get("source_food_id"),
            "is_verified": data.get("is_verified", False),
            "density": data.get("density", 1.0),
            "extra_nutrients": data.get("extra_nutrients"),
        }
        update_fields = {
            key: value for key, value in values.items() if key != "name_normalized"
        }
        if values["source_namespace"] is None or values["source_food_id"] is None:
            update_fields.pop("source_namespace", None)
            update_fields.pop("source_food_id", None)
        stmt = pg_insert(FoodReferenceModel).values(**values)
        on_conflict_kwargs: dict[str, Any] = {
            "index_elements": ["name_normalized"],
            "set_": update_fields,
        }
        if not values["is_verified"]:
            on_conflict_kwargs["where"] = FoodReferenceModel.is_verified.is_(False)
        await self._session.execute(stmt.on_conflict_do_update(**on_conflict_kwargs))
        await self._session.flush()

        refreshed = await self._find_model_by_normalized_name(name_normalized)
        if refreshed:
            await self._sync_normalized_children(refreshed, data)
        if isinstance(refreshed, FoodReferenceModel):
            await self._integrity_repository.materialize_reference(refreshed)

    async def find_batch_by_normalized_names(
        self, names_normalized: list[str]
    ) -> dict[str, dict[str, Any]]:
        if not names_normalized:
            return {}

        stmt = (
            select(FoodReferenceModel)
            .where(FoodReferenceModel.name_normalized.in_(names_normalized))
            .where(self._integrity_repository.public_eligibility_clause())
            .options(*_FOOD_REFERENCE_LOAD_OPTIONS)
        )
        result = await self._session.execute(stmt)
        return {
            row.name_normalized: food_reference_model_to_dict(row)
            for row in result.scalars().all()
            if row.name_normalized is not None
        }

    async def find_by_normalized_name(
        self, name_normalized: str
    ) -> dict[str, Any] | None:
        stmt = (
            select(FoodReferenceModel)
            .where(FoodReferenceModel.name_normalized == name_normalized)
            .where(self._integrity_repository.public_eligibility_clause())
            .options(*_FOOD_REFERENCE_LOAD_OPTIONS)
        )
        result = await self._session.execute(stmt)
        model = result.scalars().first()
        return food_reference_model_to_dict(model) if model else None

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
        identity_namespace = source_namespace or _source_namespace(source)
        identity_id = str(external_id) if external_id is not None else None
        existing = await self._find_model_by_source_identity(
            identity_namespace, identity_id
        )
        name_match = await self._find_model_by_normalized_name(name_normalized)
        if existing is not None and name_match is None:
            raise ValueError("source identity/name collision requires review")
        if (
            existing is not None
            and name_match is not None
            and name_match.id != existing.id
        ):
            raise ValueError("source identity/name collision requires review")
        if existing is None:
            existing = name_match
        if existing is not None and _identity_conflicts(
            existing, identity_namespace, identity_id
        ):
            raise ValueError("source identity/name collision requires review")
        if existing is not None and existing.is_verified and not is_verified:
            return food_reference_model_to_dict(existing)
        if is_verified:
            self._integrity_policy.require_valid(
                {
                    "protein_100g": protein_100g,
                    "carbs_100g": carbs_100g,
                    "fat_100g": fat_100g,
                    "fiber_100g": fiber_100g,
                    "sugar_100g": sugar_100g,
                },
                require_energy=False,
                require_metric_basis=False,
            )

        values = {
            "name": name,
            "name_normalized": name_normalized,
            "protein_100g": protein_100g,
            "carbs_100g": carbs_100g,
            "fat_100g": fat_100g,
            "fiber_100g": fiber_100g,
            "sugar_100g": sugar_100g,
            "source": source,
            "is_verified": is_verified,
            "region": "global",
            "source_namespace": identity_namespace if identity_id else None,
            "source_food_id": identity_id,
        }
        update_fields = {
            k: v
            for k, v in values.items()
            if k not in {"name_normalized", "source_namespace", "source_food_id"}
        }
        if identity_id:
            update_fields.update(
                {
                    "source_namespace": identity_namespace,
                    "source_food_id": identity_id,
                }
            )
        stmt = pg_insert(FoodReferenceModel).values(**values)
        on_conflict_kwargs: dict[str, Any] = {
            "index_elements": ["name_normalized"],
            "set_": update_fields,
        }
        if not is_verified:
            on_conflict_kwargs["where"] = FoodReferenceModel.is_verified.is_(False)
        await self._session.execute(stmt.on_conflict_do_update(**on_conflict_kwargs))
        await self._session.flush()

        refreshed = await self._find_model_by_normalized_name(name_normalized)
        if isinstance(refreshed, FoodReferenceModel):
            await self._sync_normalized_children(refreshed, {})
        if isinstance(refreshed, FoodReferenceModel):
            await self._integrity_repository.materialize_reference(refreshed)
        return food_reference_model_to_dict(refreshed) if refreshed else None

    async def _find_model_by_normalized_name(
        self, name_normalized: str
    ) -> FoodReferenceModel | None:
        result = await self._session.execute(
            select(FoodReferenceModel)
            .where(FoodReferenceModel.name_normalized == name_normalized)
            .options(*_FOOD_REFERENCE_LOAD_OPTIONS)
        )
        return result.scalars().first()

    async def _find_model_by_source_identity(
        self, source_namespace: str | None, source_food_id: str | None
    ) -> FoodReferenceModel | None:
        if not source_namespace or not source_food_id:
            return None
        result = await self._session.execute(
            select(FoodReferenceModel)
            .where(FoodReferenceModel.source_namespace == source_namespace)
            .where(FoodReferenceModel.source_food_id == source_food_id)
            .options(*_FOOD_REFERENCE_LOAD_OPTIONS)
        )
        return result.scalars().first()

    async def _ensure_source_identity_collision_safe(
        self,
        name_normalized: str,
        source_namespace: str,
        source_food_id: str,
    ) -> None:
        existing = await self._find_model_by_source_identity(
            source_namespace, source_food_id
        )
        name_match = await self._find_model_by_normalized_name(name_normalized)
        if existing is not None and name_match is None:
            raise ValueError("source identity/name collision requires review")
        if (
            existing is not None
            and name_match is not None
            and name_match.id != existing.id
        ):
            raise ValueError("source identity/name collision requires review")
        if name_match is not None and _identity_conflicts(
            name_match, source_namespace, source_food_id
        ):
            raise ValueError("source identity/name collision requires review")

    async def _find_model_by_barcode(self, barcode: str) -> FoodReferenceModel | None:
        result = await self._session.execute(
            select(FoodReferenceModel)
            .where(FoodReferenceModel.barcode == barcode)
            .options(*_FOOD_REFERENCE_LOAD_OPTIONS)
        )
        return result.scalars().first()

    async def _find_after_upsert(
        self,
        values: dict[str, Any],
    ) -> FoodReferenceModel | None:
        if values.get("barcode"):
            stmt = select(FoodReferenceModel).where(
                FoodReferenceModel.barcode == values["barcode"]
            )
        elif values.get("fdc_id"):
            stmt = select(FoodReferenceModel).where(
                FoodReferenceModel.fdc_id == values["fdc_id"]
            )
        else:
            return None
        result = await self._session.execute(
            stmt.options(*_FOOD_REFERENCE_LOAD_OPTIONS)
        )
        return result.scalars().first()

    async def _sync_normalized_children(
        self,
        model: FoodReferenceModel,
        data: dict[str, Any],
    ) -> None:
        serving_sizes = data.get("serving_sizes") or data.get("allowed_units")
        extra_nutrients = data.get("extra_nutrients")
        existing_servings = _loaded_collection(model, "serving_size_rows")
        if serving_sizes is not None:
            _replace_relationship_collection(
                model,
                "serving_size_rows",
                build_food_reference_serving_rows(serving_sizes),
            )
        elif existing_servings is not None and not existing_servings:
            _replace_relationship_collection(
                model,
                "serving_size_rows",
                build_food_reference_serving_rows([{"name": "g", "grams": 1.0}]),
            )
        if extra_nutrients is not None:
            _replace_relationship_collection(
                model,
                "nutrient_rows",
                build_food_reference_nutrient_rows(extra_nutrients),
            )
        await self._session.flush()


def _catalog_seed_candidate_projection(row: Any) -> FoodReferenceNutritionProjection:
    return FoodReferenceNutritionProjection(
        id=int(row.id),
        name=str(row.name),
        source=str(row.source),
        source_namespace=getattr(row, "source_namespace", None),
        source_food_id=getattr(row, "source_food_id", None),
        is_verified=bool(row.is_verified),
        protein_100g=row.protein_100g,
        carbs_100g=row.carbs_100g,
        fat_100g=row.fat_100g,
        fiber_100g=row.fiber_100g or 0.0,
        sugar_100g=row.sugar_100g or 0.0,
        density_g_ml=row.density,
        name_normalized=row.name_normalized,
    )


def _projection_is_integrity_valid(
    projection: FoodReferenceNutritionProjection,
    policy: NutritionIntegrityPolicy,
) -> bool:
    return policy.evaluate(
        {
            "protein_100g": projection.protein_100g,
            "carbs_100g": projection.carbs_100g,
            "fat_100g": projection.fat_100g,
            "fiber_100g": projection.fiber_100g,
            "sugar_100g": projection.sugar_100g,
        },
        require_energy=False,
        require_metric_basis=False,
    ).accepted


def _truncate_category(value: Any) -> str | None:
    if value is None:
        return None
    category = str(value).strip()
    return category[:100] or None


def _dedupe_search_projections(
    models: list[FoodReferenceModel],
    limit: int,
    *,
    integrity_policy: NutritionIntegrityPolicy | None = None,
    seen: set[str] | None = None,
) -> list[FoodReferenceSearchProjection]:
    if limit <= 0:
        return []
    seen = seen if seen is not None else set()
    projections: list[FoodReferenceSearchProjection] = []
    for model in models:
        if not model.is_verified:
            continue
        materialized_status = getattr(model, "integrity_status", None)
        if isinstance(materialized_status, str) and materialized_status != "valid":
            continue
        if (
            integrity_policy is not None
            and not integrity_policy.evaluate(
                food_reference_model_to_integrity_data(model),
                require_energy=False,
                require_metric_basis=False,
            ).accepted
        ):
            continue
        normalized_name = model.name_normalized or normalize_food_name(model.name)
        identity_namespace = getattr(model, "source_namespace", None)
        identity_id = getattr(model, "source_food_id", None)
        if (
            identity_namespace in {"fatsecret", "openfoodfacts", "usda_fdc"}
            and identity_id
        ):
            dedupe_key = f"{identity_namespace}:{identity_id}"
        else:
            dedupe_key = normalized_name
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        projections.append(
            FoodReferenceSearchProjection(
                id=model.id,
                name=model.name,
                name_vi=model.name_vi,
                name_normalized=model.name_normalized,
                brand=model.brand,
                source=model.source,
                source_namespace=getattr(model, "source_namespace", None),
                source_food_id=getattr(model, "source_food_id", None),
                is_verified=model.is_verified,
                protein_100g=model.protein_100g,
                carbs_100g=model.carbs_100g,
                fat_100g=model.fat_100g,
                fiber_100g=model.fiber_100g or 0.0,
                sugar_100g=model.sugar_100g or 0.0,
                serving_size=model.serving_size,
                allowed_units=food_reference_model_to_dict(model)["allowed_units"],
            )
        )
        if len(projections) >= limit:
            break
    return projections


def _source_namespace(source: str | None) -> str | None:
    normalized = str(source or "").strip().lower()
    if normalized in {"fatsecret", "openfoodfacts", "provider"}:
        return normalized if normalized != "provider" else None
    if normalized in {"usda", "usda_fdc", "fooddata_central"}:
        return "usda_fdc"
    return None


def _source_identity_from_data(data: dict[str, Any]) -> tuple[str | None, str | None]:
    namespace = data.get("source_namespace")
    source_id = data.get("source_food_id")
    if namespace is None and source_id is None:
        return None, None
    if namespace is None or source_id is None:
        raise ValueError("source identity requires namespace and id")
    normalized_namespace = str(namespace).strip().lower()
    normalized_id = str(source_id).strip()
    if not normalized_namespace or not normalized_id:
        raise ValueError("source identity requires namespace and id")
    return normalized_namespace, normalized_id


def _identity_conflicts(
    model: FoodReferenceModel,
    source_namespace: str | None,
    source_food_id: str | None,
) -> bool:
    existing_namespace = getattr(model, "source_namespace", None)
    existing_id = getattr(model, "source_food_id", None)
    if not source_namespace or not source_food_id:
        return False
    return (existing_namespace, existing_id) != (source_namespace, source_food_id)
