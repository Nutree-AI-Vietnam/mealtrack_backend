"""Find a day's everyday meals and import each one that the catalog accepts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from src.app.services.catalog_meal_seed_import_service import CatalogSeedImportSummary
from src.domain.services.everyday_catalog import (
    EVERYDAY_CATALOG_SOURCE,
    MAX_ATTEMPTS_PER_MEAL,
    EverydayMealDraft,
    EverydaySlot,
    build_import_manifest,
    day_start,
    draft_from_payload,
    local_day,
    plan_slots,
)
from src.domain.services.meal_recommendation.catalog_recipe_seed_validator import (
    validate_catalog_seed_manifest,
)


class EverydayMealFinder(Protocol):
    """Ask the model for one dish. Implementations perform the provider call."""

    async def find(
        self, slot: EverydaySlot, *, exclude_names: tuple[str, ...]
    ) -> object | None:
        """Return the raw model payload, or None when the provider fails."""


class EverydayCatalogStore(Protocol):
    """Catalog reads and one-recipe writes used by the daily job."""

    async def count_imported_since(self, source_name: str, since: datetime) -> int:
        """Count meals this job already committed since the given moment."""

    async def import_manifest(
        self, manifest: dict, *, dry_run: bool
    ) -> CatalogSeedImportSummary:
        """Import one manifest. A failed summary must not leave a partial row."""


@dataclass(frozen=True)
class EverydayImportResult:
    """What one run did. Names are the meals actually inserted."""

    day: str
    requested: int
    already_imported: int
    inserted: int
    skipped: int
    failed: int
    names: tuple[str, ...]
    dry_run: bool


class EverydayCatalogImportService:
    """Fill today's quota, then stop. A second run the same day imports nothing."""

    def __init__(self, finder: EverydayMealFinder, store: EverydayCatalogStore):
        self._finder = finder
        self._store = store

    async def run(
        self,
        *,
        now: datetime,
        batch_size: int,
        market: str | None = None,
        dry_run: bool = False,
    ) -> EverydayImportResult:
        day = local_day(now)
        slots = plan_slots(day, batch_size, market=market)
        already = await self._store.count_imported_since(
            EVERYDAY_CATALOG_SOURCE, day_start(day)
        )
        remaining = max(0, len(slots) - already)
        attempt_budget = remaining * MAX_ATTEMPTS_PER_MEAL
        inserted_names: list[str] = []
        skipped = 0
        failed = 0
        excluded: list[str] = []
        attempts = 0
        slot_index = 0
        while remaining > 0 and attempts < attempt_budget:
            slot = slots[slot_index % len(slots)]
            slot_index += 1
            attempts += 1
            payload = await self._finder.find(slot, exclude_names=tuple(excluded))
            draft = draft_from_payload(slot, payload) if payload is not None else None
            if draft is None or draft.name.casefold() in {
                name.casefold() for name in excluded
            }:
                failed += 1
                continue
            outcome = await self._import_one(draft, dry_run=dry_run)
            excluded.append(draft.name)
            if outcome == "inserted":
                inserted_names.append(draft.name)
                remaining -= 1
            elif outcome == "skipped":
                skipped += 1
            else:
                failed += 1
        return EverydayImportResult(
            day=day.isoformat(),
            requested=len(slots),
            already_imported=already,
            inserted=len(inserted_names),
            skipped=skipped,
            failed=failed,
            names=tuple(inserted_names),
            dry_run=dry_run,
        )

    async def _import_one(self, draft: EverydayMealDraft, *, dry_run: bool) -> str:
        manifest = build_import_manifest(draft)
        validation = validate_catalog_seed_manifest(
            manifest,
            expected_recipe_count=1,
            min_per_cuisine_meal_type=0,
            expected_cuisine_counts=None,
            allow_declared_expected_count_mismatch=True,
            allowed_cuisines=None,
            required_cuisines=(),
        )
        if not validation.is_valid:
            return "failed"
        summary = await self._store.import_manifest(manifest, dry_run=dry_run)
        if summary.inserted > 0 and summary.is_successful:
            return "inserted"
        if summary.skipped_existing > 0 and summary.is_successful:
            return "skipped"
        return "failed"
