"""Adopting FatSecret search hits into the durable food catalog."""

import logging

import pytest

from src.app.services.food_search_provider_adoption import (
    adopt_provider_hits,
    is_adoptable_provider_hit,
)

_LOGGER = "src.app.services.food_search_provider_adoption"


def _hit(**overrides):
    hit = {
        "source": "fatsecret",
        "source_food_id": "33890",
        "description": "Phở bò",
        "canonical_name": "Beef pho",
        "metric_serving_amount": 100.0,
        "protein_100g": 6.0,
        "carbs_100g": 10.0,
        "fat_100g": 2.0,
    }
    hit.update(overrides)
    return hit


class _FoodReferences:
    def __init__(self, existing=()):
        self.existing = list(existing)
        self.lookups = []
        self.adopted = []

    async def get_by_source_identities(self, identities):
        self.lookups.append(list(identities))
        return self.existing

    async def adopt_provider_food(
        self,
        namespace,
        food_id,
        english_name,
        macros,
        allowed_units,
        locale,
        display_name,
    ):
        self.adopted.append((namespace, food_id, english_name, locale, display_name))
        return {"id": 100 + len(self.adopted)}


class _Uow:
    def __init__(self, food_references):
        self.food_references = food_references

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _FailingUow:
    async def __aenter__(self):
        raise ConnectionError("database unavailable")

    async def __aexit__(self, *exc_info):
        return False


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, True),
        ({"source_food_id": None, "food_id": "33890"}, True),
        ({"fat_100g": 0.0}, True),
        ({"source": "openfoodfacts"}, False),
        ({"source_food_id": None}, False),
        ({"metric_serving_amount": None}, False),
        ({"protein_100g": None}, False),
        ({"carbs_100g": None}, False),
        ({"fat_100g": None}, False),
    ],
)
def test_only_fully_resolved_fatsecret_hits_are_adoptable(overrides, expected):
    assert is_adoptable_provider_hit(_hit(**overrides)) is expected


@pytest.mark.asyncio
async def test_hit_already_in_the_catalog_reuses_its_row():
    references = _FoodReferences(
        existing=[{"id": 9, "source_namespace": "fatsecret", "source_food_id": "33890"}]
    )
    hit = _hit()

    await adopt_provider_hits([hit], locale="vi", uow_factory=lambda: _Uow(references))

    assert hit["food_reference_id"] == 9
    assert references.lookups == [[("fatsecret", "33890")]]
    assert references.adopted == []


@pytest.mark.asyncio
async def test_new_hit_is_adopted_and_unresolved_hits_are_left_alone():
    references = _FoodReferences()
    resolved = _hit()
    unresolved = _hit(source_food_id="555", metric_serving_amount=None)

    await adopt_provider_hits(
        [resolved, unresolved], locale="vi", uow_factory=lambda: _Uow(references)
    )

    assert references.adopted == [("fatsecret", "33890", "Beef pho", "vi", "Phở bò")]
    assert resolved["food_reference_id"] == 101
    assert "food_reference_id" not in unresolved


@pytest.mark.asyncio
async def test_without_adoptable_hits_no_unit_of_work_is_opened():
    opened = []

    def factory():
        opened.append(True)
        return _Uow(_FoodReferences())

    await adopt_provider_hits(
        [_hit(protein_100g=None)], locale="en", uow_factory=factory
    )
    await adopt_provider_hits([_hit()], locale="en", uow_factory=None)

    assert opened == []


@pytest.mark.asyncio
async def test_unit_of_work_failure_is_logged_not_raised(caplog):
    hit = _hit()

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await adopt_provider_hits([hit], locale="en", uow_factory=_FailingUow)

    assert "food_reference_id" not in hit
    assert [
        record.getMessage() for record in caplog.records if record.name == _LOGGER
    ] == ["food search adopt failed"]
