"""Which integrity writes flush cached food search pages.

The catalog generation is part of every cached search key, so bumping it
throws the whole search cache away. Writes that no cached page can reflect
must leave it alone; the integrity event is still recorded either way.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.domain.services.nutrition_integrity_policy import (
    NUTRITION_INTEGRITY_POLICY_VERSION,
)
from src.infra.database.models.food_reference_model import FoodReferenceModel
from src.infra.database.models.food_reference_serving_size import (
    FoodReferenceServingSizeModel,
)
from src.infra.database.models.nutrition_integrity import (
    FoodReferenceIntegrityEventModel,
)
from src.infra.repositories.food_reference_integrity_repository import (
    FoodReferenceIntegrityRepository,
    food_reference_integrity_digest,
)

_GENERATION = 7


@dataclass
class _ControlRow:
    active_policy_version: str = NUTRITION_INTEGRITY_POLICY_VERSION
    catalog_integrity_generation: int = _GENERATION
    activation_run_id: str | None = None
    deployed_revision: str | None = None
    updated_at: None = None


class _Result:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _Dialect:
    def __init__(self, name):
        self.name = name


class _Bind:
    def __init__(self, dialect_name):
        self.dialect = _Dialect(dialect_name)


class _Session:
    """Answers every SELECT with the control row; ``scalar`` is the insert probe."""

    def __init__(self, *, dialect="postgresql", inserted_now=False):
        self.bind = _Bind(dialect)
        self.control = _ControlRow()
        self.inserted_now = inserted_now
        self.probes = 0
        self.added = []

    async def execute(self, statement, params=None):
        return _Result(self.control)

    async def scalar(self, statement):
        self.probes += 1
        return self.inserted_now

    def add(self, instance):
        self.added.append(instance)

    async def flush(self):
        return None

    def events(self):
        return [
            item
            for item in self.added
            if isinstance(item, FoodReferenceIntegrityEventModel)
        ]

    def generation(self):
        return self.control.catalog_integrity_generation


def _chicken_breast() -> FoodReferenceModel:
    model = FoodReferenceModel(
        id=42,
        name="Chicken breast",
        source="fatsecret",
        protein_100g=31.0,
        carbs_100g=0.0,
        fat_100g=3.6,
        fiber_100g=0.0,
        sugar_100g=0.0,
        density=1.0,
        is_verified=True,
        serving_sizes=None,
    )
    model.serving_size_rows = [
        FoodReferenceServingSizeModel(
            id=1, position=0, name="g", grams=1.0, milliliters=None, is_default=True
        ),
        FoodReferenceServingSizeModel(
            id=2,
            position=1,
            name="breast",
            grams=120.0,
            milliliters=None,
            is_default=False,
        ),
    ]
    model.integrity_status = "unknown"
    model.integrity_policy_version = None
    model.integrity_input_digest = None
    return model


def _materialized(
    model: FoodReferenceModel,
    *,
    status: str = "valid",
    policy_version: str = NUTRITION_INTEGRITY_POLICY_VERSION,
) -> FoodReferenceModel:
    """Mark the row as already materialized with its current content."""
    model.integrity_status = status
    model.integrity_policy_version = policy_version
    model.integrity_input_digest = food_reference_integrity_digest(model)
    return model


async def _materialize(session: _Session, model: FoodReferenceModel):
    return await FoodReferenceIntegrityRepository(session).materialize_reference(model)


@pytest.mark.asyncio
async def test_new_content_on_a_shown_row_flushes_cached_pages():
    model = _materialized(_chicken_breast())
    model.protein_100g = 25.0
    session = _Session()

    state = await _materialize(session, model)

    assert state.status == "valid"
    assert len(session.events()) == 1
    assert session.generation() == _GENERATION + 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("inserted_now", "expected_generation"),
    [(True, _GENERATION), (False, _GENERATION + 1)],
    ids=["inserted-by-this-transaction", "older-row-first-materialized"],
)
async def test_only_rows_older_than_the_transaction_flush_on_first_materialize(
    inserted_now, expected_generation
):
    session = _Session(inserted_now=inserted_now)

    state = await _materialize(session, _chicken_breast())

    assert state.status == "valid"
    assert len(session.events()) == 1
    assert session.probes == 1
    assert session.generation() == expected_generation


@pytest.mark.asyncio
async def test_hiding_a_shown_row_flushes_cached_pages():
    model = _materialized(_chicken_breast())
    model.protein_100g = 60.0
    model.carbs_100g = 60.0
    session = _Session()

    state = await _materialize(session, model)

    assert state.status == "quarantined"
    assert session.generation() == _GENERATION + 1


@pytest.mark.asyncio
async def test_changes_to_a_quarantined_row_keep_cached_pages():
    model = _chicken_breast()
    model.protein_100g = 60.0
    model.carbs_100g = 60.0
    _materialized(model, status="quarantined")
    model.protein_100g = 31.0
    model.carbs_100g = 0.0
    session = _Session()

    state = await _materialize(session, model)

    assert state.status == "valid"
    assert len(session.events()) == 1
    assert session.probes == 0
    assert session.generation() == _GENERATION


@pytest.mark.asyncio
async def test_a_policy_version_only_change_keeps_cached_pages():
    model = _materialized(_chicken_breast(), policy_version="nutrition_integrity_v0")
    session = _Session()

    state = await _materialize(session, model)

    assert state.policy_version == NUTRITION_INTEGRITY_POLICY_VERSION
    assert len(session.events()) == 1
    assert session.probes == 0
    assert session.generation() == _GENERATION


@pytest.mark.asyncio
async def test_other_databases_keep_the_conservative_flush():
    session = _Session(dialect="sqlite", inserted_now=True)

    await _materialize(session, _chicken_breast())

    assert session.probes == 0
    assert session.generation() == _GENERATION + 1


@pytest.mark.asyncio
async def test_an_unchanged_row_records_nothing():
    session = _Session()

    await _materialize(session, _materialized(_chicken_breast()))

    assert session.events() == []
    assert session.probes == 0
    assert session.generation() == _GENERATION
