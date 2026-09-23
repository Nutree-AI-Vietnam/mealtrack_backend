"""Additive importer for curated meal recommendation catalog seeds."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher
from time import perf_counter
from typing import Any, NoReturn

from src.domain.ports.catalog_recipe_repository_port import (
    MAX_CATALOG_POPULARITY_RANK,
    CatalogMealRepositoryPort,
    CatalogMealSeedExisting,
    CatalogMealSeedIngredientWrite,
    CatalogMealSeedSignature,
    CatalogMealSeedWrite,
)
from src.domain.ports.food_reference_repository_port import (
    FoodReferenceNutritionProjection,
    FoodReferenceRepositoryPort,
)
from src.domain.services.meal_recommendation.ingredient_quantity_conversion_service import (
    IngredientQuantityConversionError,
    IngredientQuantityConversionService,
    ResolvedIngredientQuantity,
)
from src.domain.services.meal_suggestion.ingredient_name_normalizer import (
    normalize_food_name,
)
from src.observability import distribution_metric, increment_metric

CatalogIngredientCandidateEnricher = Callable[[str], Awaitable[bool]]


@dataclass(frozen=True)
class _FoodReferenceSearchRow:
    """Lightweight row used for ingredient search and scoring."""

    food_reference_id: int
    name: str
    name_normalized: str | None
    source: str
    is_verified: bool
    protein_100g: float | None
    carbs_100g: float | None
    fat_100g: float | None
    fiber_100g: float
    sugar_100g: float
    density_g_ml: float | None


@dataclass(frozen=True)
class CatalogSeedResolutionCandidate:
    """Candidate food-reference match for one unresolved ingredient."""

    food_reference_id: int
    name: str
    name_normalized: str | None
    source: str
    is_verified: bool
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "food_reference_id": self.food_reference_id,
            "name": self.name,
            "name_normalized": self.name_normalized,
            "source": self.source,
            "is_verified": self.is_verified,
            "score": round(self.score, 4),
        }


@dataclass(frozen=True)
class CatalogSeedResolutionIssue:
    """Ingredient resolution issue with ranked candidates."""

    recipe_index: int
    recipe_key: str
    ingredient_index: int
    ingredient_name: str
    normalized_name: str
    reason: str
    candidates: tuple[CatalogSeedResolutionCandidate, ...] = field(
        default_factory=tuple
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_index": self.recipe_index,
            "recipe_key": self.recipe_key,
            "ingredient_index": self.ingredient_index,
            "ingredient_name": self.ingredient_name,
            "normalized_name": self.normalized_name,
            "reason": self.reason,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


@dataclass(frozen=True)
class CatalogSeedReviewRequired:
    """Seed row withheld until a human reviews the duplicate disposition."""

    recipe_index: int
    recipe_key: str
    reason: str
    matched_catalog_key: str
    ingredient_jaccard: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_index": self.recipe_index,
            "recipe_key": self.recipe_key,
            "reason": self.reason,
            "matched_catalog_key": self.matched_catalog_key,
            "ingredient_jaccard": round(self.ingredient_jaccard, 4),
        }


@dataclass(frozen=True)
class CatalogSeedCandidateEnrichmentSummary:
    """Outcome of caching provider candidates for later catalog review."""

    attempted: int = 0
    enriched: int = 0
    skipped_existing: int = 0


@dataclass(frozen=True)
class CatalogSeedUnverifiedReference:
    """A pinned manifest reference blocked by the publication verification gate."""

    recipe_index: int
    recipe_key: str
    ingredient_index: int
    ingredient_name: str
    food_reference_id: int
    food_reference_name: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_index": self.recipe_index,
            "recipe_key": self.recipe_key,
            "ingredient_index": self.ingredient_index,
            "ingredient_name": self.ingredient_name,
            "food_reference_id": self.food_reference_id,
            "food_reference_name": self.food_reference_name,
            "source": self.source,
        }


@dataclass(frozen=True)
class CatalogSeedImportSummary:
    """Import outcome for CLI reporting and tests."""

    inserted: int = 0
    updated: int = 0
    skipped_existing: int = 0
    dry_run: bool = False
    errors: tuple[str, ...] = field(default_factory=tuple)
    resolution_issues: tuple[CatalogSeedResolutionIssue, ...] = field(
        default_factory=tuple
    )
    unverified_references: tuple[CatalogSeedUnverifiedReference, ...] = field(
        default_factory=tuple
    )
    review_required: tuple[CatalogSeedReviewRequired, ...] = field(
        default_factory=tuple
    )
    inserted_catalog_keys: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_successful(self) -> bool:
        return not self.errors and not self.review_required

    def resolution_report(self) -> dict[str, Any]:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped_existing": self.skipped_existing,
            "dry_run": self.dry_run,
            "errors": list(self.errors),
            "issues": [issue.to_dict() for issue in self.resolution_issues],
            "unverified_references": [
                reference.to_dict() for reference in self.unverified_references
            ],
            "review_required": [item.to_dict() for item in self.review_required],
        }


class CatalogSeedImportError(ValueError):
    """Raised when a seed manifest cannot be imported safely."""


class CatalogSeedUnverifiedReferenceError(CatalogSeedImportError):
    """Raised when a manifest pins an unverified food reference."""

    def __init__(self, issue: CatalogSeedUnverifiedReference) -> None:
        self.issue = issue
        super().__init__(
            f"recipes[{issue.recipe_index}].ingredients[{issue.ingredient_index}] "
            f"food_reference_not_verified: Food reference {issue.food_reference_id} "
            "is not verified."
        )


class CatalogSeedResolutionError(CatalogSeedImportError):
    """Raised when an ingredient needs manual resolution."""

    def __init__(self, issue: CatalogSeedResolutionIssue) -> None:
        self.issue = issue
        candidate_text = ", ".join(
            f"{item.food_reference_id}:{item.name}:{item.score:.2f}"
            for item in issue.candidates[:5]
        )
        suffix = f"; candidates: {candidate_text}" if candidate_text else ""
        super().__init__(
            f"recipes[{issue.recipe_index}].ingredients[{issue.ingredient_index}] "
            f"{issue.reason}: '{issue.ingredient_name}' "
            f"normalized='{issue.normalized_name}'{suffix}"
        )


class CatalogSeedResolutionErrors(CatalogSeedImportError):
    """All ingredient-resolution decisions required for one recipe."""

    def __init__(self, issues: list[CatalogSeedResolutionIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__(
            "; ".join(str(CatalogSeedResolutionError(issue)) for issue in issues)
        )


@dataclass(frozen=True)
class _PreparedCatalogSeed:
    recipe_index: int
    seed: CatalogMealSeedWrite
    signature: CatalogMealSeedSignature


class CatalogMealSeedImporter:
    """Import catalog meals additively with exact duplicate protection."""

    def __init__(
        self,
        catalog_repository: CatalogMealRepositoryPort,
        food_reference_repository: FoodReferenceRepositoryPort,
        *,
        dry_run: bool = False,
        approved_mappings: dict[str, int] | None = None,
        auto_resolve_threshold: float | None = 0.92,
        resolve_all_best_effort: bool = False,
        candidate_enricher: CatalogIngredientCandidateEnricher | None = None,
        converter: IngredientQuantityConversionService | None = None,
    ) -> None:
        self._catalog_repository = catalog_repository
        self._food_reference_repository = food_reference_repository
        self._dry_run = dry_run
        self._approved_mappings = {
            normalize_food_name(key): int(value)
            for key, value in (approved_mappings or {}).items()
        }
        self._auto_resolve_threshold = auto_resolve_threshold
        self._resolve_all_best_effort = resolve_all_best_effort
        self._candidate_enricher = candidate_enricher
        self._enriched_names: set[str] = set()
        self._converter = converter or IngredientQuantityConversionService(
            allow_unverified=resolve_all_best_effort,
            allow_unapproved_sources=resolve_all_best_effort,
            allow_implausible_macros=resolve_all_best_effort,
            allow_common_unit_fallbacks=resolve_all_best_effort,
        )
        self._candidate_rows: list[_FoodReferenceSearchRow] | None = None
        self._pending_popularity_updates: list[tuple[str, int | None]] = []

    def with_options(
        self,
        *,
        dry_run: bool,
        approved_mappings: dict[str, int],
        auto_resolve_threshold: float | None,
        resolve_all_best_effort: bool,
    ) -> CatalogMealSeedImporter:
        """Create a request-specific importer using the same repository ports."""

        return CatalogMealSeedImporter(
            self._catalog_repository,
            self._food_reference_repository,
            dry_run=dry_run,
            approved_mappings=approved_mappings,
            auto_resolve_threshold=auto_resolve_threshold,
            resolve_all_best_effort=resolve_all_best_effort,
            candidate_enricher=self._candidate_enricher,
        )

    async def import_manifest(
        self, manifest: dict[str, Any]
    ) -> CatalogSeedImportSummary:
        started = perf_counter()
        self._pending_popularity_updates = []
        skipped = 0
        errors: list[str] = []
        resolution_issues: list[CatalogSeedResolutionIssue] = []
        unverified_references: list[CatalogSeedUnverifiedReference] = []
        review_required: list[CatalogSeedReviewRequired] = []
        prepared: list[_PreparedCatalogSeed] = []
        signatures = await self._catalog_repository.list_seed_signatures()
        for index, recipe in enumerate(manifest.get("recipes", [])):
            try:
                prepared_seed = await self._prepare_recipe(recipe, index)
            except CatalogSeedResolutionErrors as exc:
                errors.extend(
                    str(CatalogSeedResolutionError(issue)) for issue in exc.issues
                )
                resolution_issues.extend(exc.issues)
                continue
            except CatalogSeedResolutionError as exc:
                errors.append(str(exc))
                resolution_issues.append(exc.issue)
                continue
            except CatalogSeedUnverifiedReferenceError as exc:
                errors.append(str(exc))
                unverified_references.append(exc.issue)
                continue
            except CatalogSeedImportError as exc:
                errors.append(str(exc))
                continue
            if prepared_seed is None:
                skipped += 1
                continue
            review = _near_duplicate_review(index, prepared_seed.signature, signatures)
            if review is not None:
                review_required.append(review)
                continue
            prepared.append(prepared_seed)
            signatures.append(prepared_seed.signature)
        if errors or review_required or self._dry_run:
            summary = CatalogSeedImportSummary(
                inserted=len(prepared) if self._dry_run and not errors else 0,
                skipped_existing=skipped,
                dry_run=self._dry_run,
                errors=tuple(errors),
                resolution_issues=tuple(resolution_issues),
                unverified_references=tuple(unverified_references),
                review_required=tuple(review_required),
            )
            _record_seed_import_metrics(summary, started)
            return summary

        if prepared:
            (
                to_insert,
                skipped_after_lock,
                lock_errors,
                lock_reviews,
            ) = await self._recheck_under_lock(prepared)
        else:
            await self._catalog_repository.lock_seed_import()
            to_insert, skipped_after_lock, lock_errors, lock_reviews = [], 0, [], []
        skipped += skipped_after_lock
        if lock_errors or lock_reviews:
            summary = CatalogSeedImportSummary(
                inserted=0,
                skipped_existing=skipped,
                dry_run=self._dry_run,
                errors=tuple(lock_errors),
                review_required=tuple(lock_reviews),
            )
            _record_seed_import_metrics(summary, started)
            return summary

        for item in to_insert:
            await self._catalog_repository.add_seed_meal(item.seed)
        for catalog_key, popularity_rank in self._pending_popularity_updates:
            await self._catalog_repository.update_popularity_rank(
                catalog_key=catalog_key,
                popularity_rank=popularity_rank,
            )
        summary = CatalogSeedImportSummary(
            inserted=len(to_insert),
            updated=len(self._pending_popularity_updates),
            skipped_existing=skipped,
            dry_run=self._dry_run,
            inserted_catalog_keys=tuple(item.seed.catalog_key for item in to_insert),
        )
        _record_seed_import_metrics(summary, started)
        return summary

    async def load_meals_by_catalog_keys(self, catalog_keys: tuple[str, ...]) -> list:
        """Load hydrated catalog meals for insight warmup after import."""

        if not catalog_keys:
            return []
        wanted = set(catalog_keys)
        meals = await self._catalog_repository.list_active_meals()
        return [meal for meal in meals if meal.catalog_key in wanted]

    async def _prepare_recipe(
        self,
        recipe: dict[str, Any],
        index: int,
    ) -> _PreparedCatalogSeed | None:
        catalog_key = str(recipe["recipe_key"]).strip()
        resolved_ingredients = await self._resolve_ingredients(recipe, index)
        content_hash = _content_hash(recipe, resolved_ingredients)
        existing = await self._find_existing(catalog_key, content_hash)
        if existing is not None:
            if existing.catalog_key == catalog_key:
                if existing.content_hash != content_hash:
                    raise CatalogSeedImportError(
                        f"recipes[{index}] catalog_key already exists with different content: "
                        f"{catalog_key}"
                    )
                if "popularity_rank" in recipe:
                    popularity_rank = _optional_popularity_rank(
                        recipe.get("popularity_rank")
                    )
                    if not self._dry_run:
                        self._pending_popularity_updates.append(
                            (catalog_key, popularity_rank)
                        )
            return None

        seed_ingredients = []
        for ingredient_index, item in enumerate(resolved_ingredients):
            if item.food_reference_id is None:
                raise CatalogSeedImportError(
                    f"recipes[{index}] resolved ingredient is missing food_reference_id"
                )
            seed_ingredients.append(
                CatalogMealSeedIngredientWrite(
                    food_reference_id=item.food_reference_id,
                    display_name=item.display_name,
                    quantity=item.quantity,
                    unit=item.unit,
                    category=str(
                        recipe.get("ingredients", [])[ingredient_index].get(
                            "category", "pantry"
                        )
                    )
                    .strip()
                    .casefold(),
                )
            )

        seed = CatalogMealSeedWrite(
            catalog_key=catalog_key,
            content_hash=content_hash,
            name=str(recipe["name"]).strip(),
            cuisine=str(recipe["cuisine"]).strip(),
            description=_optional_string(recipe.get("description")),
            image_url=_optional_string(recipe.get("image_url")),
            meal_types=tuple(str(item).strip() for item in recipe["meal_types"]),
            popularity_rank=_optional_popularity_rank(recipe.get("popularity_rank")),
            ingredients=tuple(seed_ingredients),
            source_name=_optional_string(recipe.get("source_name")),
            source_url=_optional_string(recipe.get("source_url")),
            prep_time_minutes=_optional_non_negative_int(
                recipe.get("prep_time_minutes")
            ),
            cook_time_minutes=_optional_non_negative_int(
                recipe.get("cook_time_minutes")
            ),
            tag=_optional_string(recipe.get("tag")),
            allergens=_optional_string(recipe.get("allergens")),
            summary=_optional_string(recipe.get("summary")),
            equipment=_optional_string(recipe.get("equipment")),
            base_servings=_optional_positive_int(recipe.get("base_servings")),
            serving_source=_optional_string(recipe.get("serving_source")),
            serving_confidence=_serving_confidence(recipe.get("serving_confidence")),
            steps=tuple(_step_payloads(recipe.get("steps"))),
        )
        return _PreparedCatalogSeed(
            recipe_index=index,
            seed=seed,
            signature=_seed_signature(seed, content_hash),
        )

    async def _recheck_under_lock(
        self,
        prepared: list[_PreparedCatalogSeed],
    ) -> tuple[
        list[_PreparedCatalogSeed],
        int,
        list[str],
        list[CatalogSeedReviewRequired],
    ]:
        if not prepared:
            return [], 0, [], []
        await self._catalog_repository.lock_seed_import()
        signatures = await self._catalog_repository.list_seed_signatures()
        to_insert: list[_PreparedCatalogSeed] = []
        skipped = 0
        errors: list[str] = []
        review_required: list[CatalogSeedReviewRequired] = []
        for item in prepared:
            existing = await self._find_existing(
                item.seed.catalog_key,
                item.seed.content_hash,
            )
            if existing is not None:
                if (
                    existing.catalog_key == item.seed.catalog_key
                    and existing.content_hash != item.seed.content_hash
                ):
                    errors.append(
                        "catalog_key already exists with different content: "
                        f"{item.seed.catalog_key}"
                    )
                    continue
                skipped += 1
                continue
            review = _near_duplicate_review(
                item.recipe_index, item.signature, signatures
            )
            if review is not None:
                review_required.append(review)
                continue
            to_insert.append(item)
            signatures.append(item.signature)
        return to_insert, skipped, errors, review_required

    async def _resolve_ingredients(
        self,
        recipe: dict[str, Any],
        recipe_index: int,
    ) -> list[ResolvedIngredientQuantity]:
        resolved: list[ResolvedIngredientQuantity] = []
        issues: list[CatalogSeedResolutionIssue] = []
        for ingredient_index, ingredient in enumerate(recipe.get("ingredients", [])):
            try:
                reference = await self._resolve_food_reference(
                    ingredient,
                    recipe_key=str(recipe["recipe_key"]).strip(),
                    recipe_index=recipe_index,
                    ingredient_index=ingredient_index,
                )
            except CatalogSeedResolutionError as exc:
                issues.append(exc.issue)
                continue
            try:
                resolved.append(
                    self._converter.resolve(
                        reference=reference,
                        quantity=float(ingredient["quantity"]),
                        unit=str(ingredient["unit"]).strip(),
                        display_name=str(ingredient["name"]).strip(),
                    )
                )
            except IngredientQuantityConversionError as exc:
                if exc.code == "food_reference_not_verified":
                    raise CatalogSeedUnverifiedReferenceError(
                        CatalogSeedUnverifiedReference(
                            recipe_index=recipe_index,
                            recipe_key=str(recipe["recipe_key"]).strip(),
                            ingredient_index=ingredient_index,
                            ingredient_name=str(ingredient["name"]).strip(),
                            food_reference_id=reference.id,
                            food_reference_name=reference.name,
                            source=reference.source,
                        )
                    ) from exc
                raise CatalogSeedImportError(
                    f"recipes[{recipe_index}].ingredients[{ingredient_index}] "
                    f"{exc.code}: {exc}"
                ) from exc
        if issues:
            raise CatalogSeedResolutionErrors(issues)
        return resolved

    async def _resolve_food_reference(
        self,
        ingredient: dict[str, Any],
        recipe_key: str,
        recipe_index: int,
        ingredient_index: int,
    ) -> FoodReferenceNutritionProjection:
        food_reference_id = ingredient.get("food_reference_id")
        if food_reference_id is not None:
            reference = await self._get_reference_by_id(int(food_reference_id))
            if reference is None:
                raise CatalogSeedImportError(
                    f"recipes[{recipe_index}].ingredients[{ingredient_index}] "
                    f"food_reference_id not found: {food_reference_id}"
                )
            return reference

        name = str(ingredient["name"]).strip()
        normalized = normalize_food_name(name)
        mapped_id = self._approved_mappings.get(normalized)
        if mapped_id is not None:
            reference = await self._get_reference_by_id(mapped_id)
            if reference is None:
                raise CatalogSeedImportError(
                    f"recipes[{recipe_index}].ingredients[{ingredient_index}] "
                    f"mapped food_reference_id not found: {mapped_id}"
                )
            return reference

        matches = await self._find_reference_candidates_by_normalized_name(normalized)
        verified_matches = [item for item in matches if item.is_verified]
        if self._resolve_all_best_effort and matches:
            reference = await self._get_reference_by_id(matches[0].food_reference_id)
            if reference is not None:
                return reference
        if len(verified_matches) == 1:
            reference = await self._get_reference_by_id(
                verified_matches[0].food_reference_id
            )
            if reference is not None:
                return reference
        if len(verified_matches) > 1:
            self._raise_resolution_issue(
                recipe_key,
                recipe_index,
                ingredient_index,
                name,
                normalized,
                "ambiguous_exact_match",
                tuple(verified_matches[:5]),
            )
        if len(matches) == 1:
            self._raise_resolution_issue(
                recipe_key,
                recipe_index,
                ingredient_index,
                name,
                normalized,
                "exact_match_not_verified",
                (matches[0],),
            )

        candidates = await self._ranked_candidates(normalized)
        accepted = _auto_resolved_candidate(
            candidates,
            self._auto_resolve_threshold,
            allow_unverified=self._resolve_all_best_effort,
        )
        if accepted is not None:
            reference = await self._get_reference_by_id(accepted.food_reference_id)
            if reference is not None:
                return reference
        self._raise_resolution_issue(
            recipe_key,
            recipe_index,
            ingredient_index,
            name,
            normalized,
            "needs_review",
            tuple(candidates[:5]),
        )

    async def enrich_missing_candidates(
        self,
        manifest: dict[str, Any],
    ) -> CatalogSeedCandidateEnrichmentSummary:
        """Persist one unverified provider candidate per missing ingredient name."""

        names = {
            normalize_food_name(str(ingredient["name"]).strip()): str(
                ingredient["name"]
            ).strip()
            for recipe in manifest.get("recipes", [])
            if isinstance(recipe, dict)
            for ingredient in recipe.get("ingredients", [])
            if isinstance(ingredient, dict)
            and ingredient.get("food_reference_id") is None
            and str(ingredient.get("name", "")).strip()
        }
        attempted = 0
        enriched = 0
        skipped_existing = 0
        for normalized, name in names.items():
            if await self._find_reference_candidates_by_normalized_name(normalized):
                skipped_existing += 1
                continue
            attempted += 1
            if await self._enrich_missing_candidate(name, normalized):
                enriched += 1
        return CatalogSeedCandidateEnrichmentSummary(
            attempted=attempted,
            enriched=enriched,
            skipped_existing=skipped_existing,
        )

    async def _enrich_missing_candidate(self, name: str, normalized: str) -> bool:
        """Cache one provider candidate for review without bypassing publication gates."""

        if self._candidate_enricher is None or normalized in self._enriched_names:
            return False
        self._enriched_names.add(normalized)
        try:
            enriched = await self._candidate_enricher(name)
        except Exception:
            return False
        self._candidate_rows = None
        return enriched

    async def _find_existing(
        self,
        catalog_key: str,
        content_hash: str,
    ) -> CatalogMealSeedExisting | None:
        return await self._catalog_repository.find_seed_existing(
            catalog_key=catalog_key,
            content_hash=content_hash,
        )

    async def _get_reference_by_id(
        self,
        food_reference_id: int,
    ) -> FoodReferenceNutritionProjection | None:
        for row in await self._all_candidate_rows():
            if row.food_reference_id == food_reference_id:
                return _projection_from_search_row(row)
        return await self._food_reference_repository.get_nutrition_projection(
            food_reference_id
        )

    async def _find_reference_candidates_by_normalized_name(
        self,
        name_normalized: str,
    ) -> list[CatalogSeedResolutionCandidate]:
        rows = await self._food_reference_repository.find_catalog_seed_candidates_by_normalized_name(
            name_normalized
        )
        return [
            _candidate_from_search_row(_search_row_from_projection(row), 1.0)
            for row in rows
        ]

    async def _ranked_candidates(
        self,
        normalized_name: str,
    ) -> list[CatalogSeedResolutionCandidate]:
        rows = await self._all_candidate_rows()
        candidates = [
            _candidate_from_search_row(row, _candidate_score(normalized_name, row))
            for row in rows
        ]
        candidates = [candidate for candidate in candidates if candidate.score > 0.25]
        return sorted(
            candidates,
            key=lambda item: (
                -item.score,
                not item.is_verified,
                item.food_reference_id,
            ),
        )[:20]

    async def _all_candidate_rows(self) -> list[_FoodReferenceSearchRow]:
        if self._candidate_rows is not None:
            return self._candidate_rows
        self._candidate_rows = [
            _search_row_from_projection(row)
            for row in await self._food_reference_repository.list_catalog_seed_candidates()
        ]
        return self._candidate_rows

    def _raise_resolution_issue(
        self,
        recipe_key: str,
        recipe_index: int,
        ingredient_index: int,
        ingredient_name: str,
        normalized_name: str,
        reason: str,
        candidates: tuple[CatalogSeedResolutionCandidate, ...],
    ) -> NoReturn:
        issue = CatalogSeedResolutionIssue(
            recipe_index=recipe_index,
            recipe_key=recipe_key,
            ingredient_index=ingredient_index,
            ingredient_name=ingredient_name,
            normalized_name=normalized_name,
            reason=reason,
            candidates=candidates,
        )
        raise CatalogSeedResolutionError(issue)


def _candidate_from_search_row(
    row: _FoodReferenceSearchRow,
    score: float,
) -> CatalogSeedResolutionCandidate:
    return CatalogSeedResolutionCandidate(
        food_reference_id=row.food_reference_id,
        name=row.name,
        name_normalized=row.name_normalized,
        source=row.source,
        is_verified=row.is_verified,
        score=score,
    )


def _projection_from_search_row(
    row: _FoodReferenceSearchRow,
) -> FoodReferenceNutritionProjection:
    return FoodReferenceNutritionProjection(
        id=row.food_reference_id,
        name=row.name,
        source=row.source,
        is_verified=row.is_verified,
        protein_100g=row.protein_100g,
        carbs_100g=row.carbs_100g,
        fat_100g=row.fat_100g,
        fiber_100g=row.fiber_100g,
        sugar_100g=row.sugar_100g,
        density_g_ml=row.density_g_ml,
        servings=[],
    )


def _search_row_from_projection(
    row: FoodReferenceNutritionProjection,
) -> _FoodReferenceSearchRow:
    return _FoodReferenceSearchRow(
        food_reference_id=row.id,
        name=row.name,
        name_normalized=row.name_normalized or normalize_food_name(row.name),
        source=row.source,
        is_verified=row.is_verified,
        protein_100g=row.protein_100g,
        carbs_100g=row.carbs_100g,
        fat_100g=row.fat_100g,
        fiber_100g=row.fiber_100g,
        sugar_100g=row.sugar_100g,
        density_g_ml=row.density_g_ml,
    )


def _candidate_score(normalized_name: str, row: _FoodReferenceSearchRow) -> float:
    candidate_name = row.name_normalized or normalize_food_name(row.name)
    sequence = SequenceMatcher(None, normalized_name, candidate_name).ratio()
    source_tokens = set(normalized_name.split())
    candidate_tokens = set(candidate_name.split())
    overlap = (
        len(source_tokens.intersection(candidate_tokens)) / len(source_tokens)
        if source_tokens
        else 0.0
    )
    extra_token_penalty = max(0, len(candidate_tokens) - len(source_tokens)) * 0.03
    return max(0.0, min(1.0, (sequence * 0.7) + (overlap * 0.3) - extra_token_penalty))


def _auto_resolved_candidate(
    candidates: list[CatalogSeedResolutionCandidate],
    threshold: float | None,
    *,
    allow_unverified: bool = False,
) -> CatalogSeedResolutionCandidate | None:
    if threshold is None:
        return None
    eligible = (
        candidates
        if allow_unverified
        else [candidate for candidate in candidates if candidate.is_verified]
    )
    if not eligible:
        return None
    top = eligible[0]
    runner_up = eligible[1].score if len(eligible) > 1 else 0.0
    if allow_unverified and threshold <= 0:
        return top
    if top.score >= threshold and top.score - runner_up >= 0.08:
        return top
    return None


def normalize_catalog_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _content_hash(
    recipe: dict[str, Any],
    ingredients: list[ResolvedIngredientQuantity],
) -> str:
    ingredient_payloads = [
        {
            "food_reference_id": item.food_reference_id,
            "quantity": _canonical_decimal(item.quantity),
            "unit": normalize_catalog_text(item.unit),
        }
        for item in ingredients
    ]
    payload = {
        "version": "meal_catalog_content_v3",
        "name": normalize_catalog_text(str(recipe["name"])),
        "cuisine": normalize_catalog_text(str(recipe["cuisine"])),
        "meal_types": sorted(recipe["meal_types"]),
        "details": {
            key: recipe.get(key)
            for key in (
                "source_name",
                "source_url",
                "prep_time_minutes",
                "cook_time_minutes",
                "tag",
                "allergens",
                "summary",
                "equipment",
                "base_servings",
                "serving_source",
                "serving_confidence",
                "steps",
            )
        },
        "ingredients": sorted(
            ingredient_payloads,
            key=lambda item: (
                item["food_reference_id"],
                item["unit"],
                item["quantity"],
            ),
        ),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_decimal(value: float) -> str:
    return format(Decimal(str(value)).quantize(Decimal("0.0001")), "f")


def _optional_popularity_rank(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise CatalogSeedImportError("popularity_rank must be an integer") from None
    if value < 0 or value > MAX_CATALOG_POPULARITY_RANK:
        raise CatalogSeedImportError(
            "popularity_rank must fit a non-negative PostgreSQL INTEGER"
        )
    return value


def _optional_non_negative_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CatalogSeedImportError("recipe time must be a non-negative integer")
    return value


def _optional_positive_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CatalogSeedImportError("base_servings must be a positive integer")
    return value


def _serving_confidence(value: Any) -> str:
    if value is None:
        return "unknown"
    confidence = str(value).strip().casefold()
    if confidence not in {"verified", "estimated", "unknown"}:
        raise CatalogSeedImportError(
            "serving_confidence must be verified, estimated, or unknown"
        )
    return confidence


def _step_payloads(value: Any) -> list[tuple[int, str, str]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 50:
        raise CatalogSeedImportError("steps must be an array with at most 50 items")
    steps: list[tuple[int, str, str]] = []
    for expected, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise CatalogSeedImportError("each step must be an object")
        number = item.get("step_number", expected)
        title = _optional_string(item.get("title"))
        description = _optional_string(item.get("description"))
        if number != expected or title is None or description is None:
            raise CatalogSeedImportError(
                "steps must be consecutively numbered with title and description"
            )
        steps.append((expected, title, description))
    return steps


def _seed_signature(
    seed: CatalogMealSeedWrite,
    content_hash: str,
) -> CatalogMealSeedSignature:
    return CatalogMealSeedSignature(
        catalog_key=seed.catalog_key,
        content_hash=content_hash,
        normalized_name=normalize_catalog_text(seed.name),
        normalized_cuisine=normalize_catalog_text(seed.cuisine),
        food_reference_ids=frozenset(
            item.food_reference_id for item in seed.ingredients
        ),
    )


def _near_duplicate_review(
    recipe_index: int,
    candidate: CatalogMealSeedSignature,
    existing_signatures: list[CatalogMealSeedSignature],
) -> CatalogSeedReviewRequired | None:
    for existing in existing_signatures:
        if existing.content_hash == candidate.content_hash:
            continue
        if existing.normalized_name != candidate.normalized_name:
            continue
        if existing.normalized_cuisine != candidate.normalized_cuisine:
            continue
        jaccard = _ingredient_jaccard(
            candidate.food_reference_ids,
            existing.food_reference_ids,
        )
        if jaccard >= 0.80:
            return CatalogSeedReviewRequired(
                recipe_index=recipe_index,
                recipe_key=candidate.catalog_key,
                reason="near_duplicate",
                matched_catalog_key=existing.catalog_key,
                ingredient_jaccard=jaccard,
            )
    return None


def _ingredient_jaccard(left: frozenset[int], right: frozenset[int]) -> float:
    if not left and not right:
        return 1.0
    union = left.union(right)
    if not union:
        return 0.0
    return len(left.intersection(right)) / len(union)


def _record_seed_import_metrics(
    summary: CatalogSeedImportSummary,
    started: float,
) -> None:
    attributes = {
        "operation": "seed_import",
        "status": _seed_import_status(summary),
    }
    distribution_metric(
        "meal_catalog.seed_import.duration_ms",
        (perf_counter() - started) * 1000,
        unit="millisecond",
        attributes=attributes,
    )
    increment_metric(
        "meal_catalog.seed_import.imported",
        value=summary.inserted,
        attributes=attributes,
    )
    increment_metric(
        "meal_catalog.seed_import.skipped",
        value=summary.skipped_existing,
        attributes=attributes,
    )
    increment_metric(
        "meal_catalog.seed_import.review_required",
        value=len(summary.review_required),
        attributes=attributes,
    )
    increment_metric(
        "meal_catalog.seed_import.rejected",
        value=len(summary.errors),
        attributes=attributes,
    )


def _seed_import_status(summary: CatalogSeedImportSummary) -> str:
    if summary.errors:
        return "error"
    if summary.review_required:
        return "review"
    if summary.dry_run:
        return "dry_run"
    return "success"


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
