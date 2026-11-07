from datetime import UTC, datetime

import pytest

from src.app.services.catalog_meal_seed_import_service import CatalogSeedImportSummary
from src.app.services.everyday_catalog_import_service import (
    EverydayCatalogImportService,
)
from src.domain.services.everyday_catalog import EverydaySlot


def _payload(name: str) -> dict:
    return {
        "name": name,
        "ingredients": [
            {"name": "egg", "amount": 100, "unit": "g"},
            {"name": "rice", "amount": 150, "unit": "g"},
            {"name": "onion", "amount": 20, "unit": "g"},
        ],
        "recipe_steps": [
            {"instruction": "Cook the rice."},
            {"instruction": "Add the egg."},
        ],
        "prep_time_minutes": 15,
    }


class _Finder:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    async def find(self, slot: EverydaySlot, *, exclude_names: tuple[str, ...]):
        self.calls += 1
        if not self.payloads:
            return None
        return self.payloads.pop(0)


class _Store:
    def __init__(self, *, already=0, outcomes=None):
        self.already = already
        self.outcomes = list(outcomes or [])
        self.manifests = []

    async def count_imported_since(self, source_name: str, since: datetime) -> int:
        assert source_name == "everyday-catalog"
        assert since.tzinfo is not None
        return self.already

    async def import_manifest(self, manifest: dict, *, dry_run: bool):
        self.manifests.append(manifest)
        outcome = self.outcomes.pop(0) if self.outcomes else "inserted"
        if outcome == "inserted":
            return CatalogSeedImportSummary(inserted=1)
        if outcome == "skipped":
            return CatalogSeedImportSummary(skipped_existing=1)
        return CatalogSeedImportSummary(errors=("unresolved",))


@pytest.mark.asyncio
async def test_second_run_the_same_day_imports_nothing():
    finder = _Finder([_payload("Oatmeal")])
    result = await EverydayCatalogImportService(finder, _Store(already=8)).run(
        now=datetime(2026, 10, 29, 2, tzinfo=UTC), batch_size=8
    )
    assert result.inserted == 0
    assert finder.calls == 0


@pytest.mark.asyncio
async def test_failed_draft_is_retried_until_one_meal_imports():
    finder = _Finder([{"name": "nope"}, _payload("Chicken rice")])
    store = _Store(already=7, outcomes=["inserted"])
    result = await EverydayCatalogImportService(finder, store).run(
        now=datetime(2026, 10, 29, 2, tzinfo=UTC), batch_size=8
    )
    assert result.inserted == 1
    assert result.names == ("Chicken rice",)
    assert result.failed == 1
    assert store.manifests[0]["recipes"][0]["recipe_key"].startswith("everyday-")


@pytest.mark.asyncio
async def test_duplicate_does_not_count_toward_the_cap():
    finder = _Finder([_payload("Oatmeal"), _payload("Yogurt bowl")])
    store = _Store(already=7, outcomes=["skipped", "inserted"])
    result = await EverydayCatalogImportService(finder, store).run(
        now=datetime(2026, 10, 29, 2, tzinfo=UTC), batch_size=8
    )
    assert result.inserted == 1
    assert result.skipped == 1
    assert result.names == ("Yogurt bowl",)
