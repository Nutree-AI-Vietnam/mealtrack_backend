from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.selectable import Select

from src.domain.services.nutrition_integrity_policy import NutritionIntegrityError
from src.infra.repositories.food_reference_adopt import FoodReferenceAdoptRepository


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def scalars(self):
        return _Scalars(self._rows)


class _NestedTx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _AdoptSession:
    """Fake session for FoodReferenceAdoptRepository unit tests.

    ``execute`` branches on statement type: SELECT returns the scripted
    identity row, everything else (translation upserts) is just recorded.
    A scripted ``race_winner`` lets tests simulate a concurrent insert:
    the next ``flush()`` after ``add()`` raises IntegrityError and the
    winner row becomes visible to the reread SELECT.
    """

    def __init__(self, existing=None, race_winner=None):
        self.existing = existing
        self.race_winner = race_winner
        self.added = []
        self.translation_statements = []
        self.select_count = 0
        self.flush_count = 0
        self._pending_add = False
        self._raced = False

    async def execute(self, statement):
        if isinstance(statement, Select):
            self.select_count += 1
            return _Result([self.existing] if self.existing else [])
        self.translation_statements.append(statement)
        return _Result()

    def add(self, model):
        self.added.append(model)
        self._pending_add = True

    async def flush(self):
        self.flush_count += 1
        if self._pending_add and self.race_winner is not None and not self._raced:
            self._raced = True
            self.existing = self.race_winner
            raise IntegrityError("insert", {}, Exception("duplicate key"))
        self._pending_add = False

    def begin_nested(self):
        return _NestedTx(self)


def _existing_row(
    *,
    food_id=7,
    name="Beef",
    name_normalized="fatsecret:33890",
    verified=False,
    source_namespace="fatsecret",
    source_food_id="33890",
    protein_100g=26.0,
    carbs_100g=0.0,
    fat_100g=15.0,
):
    row = MagicMock()
    row.id = food_id
    row.name = name
    row.name_normalized = name_normalized
    row.name_vi = None
    row.brand = None
    row.barcode = None
    row.category = None
    row.region = "global"
    row.fdc_id = None
    row.protein_100g = protein_100g
    row.carbs_100g = carbs_100g
    row.fat_100g = fat_100g
    row.fiber_100g = 0.0
    row.sugar_100g = 0.0
    row.serving_size_rows = []
    row.serving_sizes = None
    row.density = 1.0
    row.serving_size = None
    row.nutrient_rows = []
    row.extra_nutrients = None
    row.source = source_namespace
    row.source_namespace = source_namespace
    row.source_food_id = source_food_id
    row.is_verified = verified
    row.image_url = None
    return row


def _patched_materialize():
    return patch(
        "src.infra.repositories.food_reference_adopt.FoodReferenceIntegrityRepository."
        "materialize_reference",
        new_callable=AsyncMock,
    )


@pytest.mark.asyncio
async def test_adopt_new_fatsecret_food_sets_identity_name_normalized():
    session = _AdoptSession(existing=None)
    repo = FoodReferenceAdoptRepository(session)

    with _patched_materialize():
        result = await repo.adopt_provider_food(
            "fatsecret",
            "33890",
            "Beef, ground, 85% lean",
            {"protein_100g": 26.0, "carbs_100g": 0.0, "fat_100g": 15.0},
            [{"name": "g", "grams": 1.0}],
            "en",
            "Beef, ground, 85% lean",
        )

    assert result["name_normalized"] == "fatsecret:33890"
    assert len(session.added) == 1
    new_model = session.added[0]
    assert new_model.name_normalized == "fatsecret:33890"
    assert new_model.source_namespace == "fatsecret"
    assert new_model.source_food_id == "33890"


@pytest.mark.asyncio
async def test_adopt_same_id_twice_reuses_row_and_freezes_verified_density():
    existing = _existing_row(verified=True, protein_100g=26.0, carbs_100g=0.0, fat_100g=15.0)
    session = _AdoptSession(existing=existing)
    repo = FoodReferenceAdoptRepository(session)

    with _patched_materialize():
        result = await repo.adopt_provider_food(
            "fatsecret",
            "33890",
            "Beef, ground, 99% lean",
            {"protein_100g": 999.0, "carbs_100g": 999.0, "fat_100g": 999.0},
            None,
            "en",
            "Beef, ground, 99% lean",
        )

    assert session.added == []
    assert result["id"] == existing.id
    assert existing.protein_100g == pytest.approx(26.0)
    assert existing.carbs_100g == pytest.approx(0.0)
    assert existing.fat_100g == pytest.approx(15.0)


@pytest.mark.asyncio
async def test_adopt_beef_seed_and_fatsecret_beef_coexist_as_two_rows():
    session = _AdoptSession(existing=None)
    repo = FoodReferenceAdoptRepository(session)

    with _patched_materialize():
        result = await repo.adopt_provider_food(
            "fatsecret",
            "33890",
            "Beef",
            {"protein_100g": 26.0, "carbs_100g": 0.0, "fat_100g": 15.0},
            [{"name": "g", "grams": 1.0}],
            "en",
            "Beef",
        )

    assert result["name_normalized"] == "fatsecret:33890"
    assert result["name_normalized"] != "beef"


@pytest.mark.asyncio
async def test_adopt_never_rewrites_a_human_seed_name_normalized():
    seed_row = _existing_row(
        name="Beef",
        name_normalized="beef",
        verified=False,
        source_namespace="fatsecret",
        source_food_id="33890",
    )
    session = _AdoptSession(existing=seed_row)
    repo = FoodReferenceAdoptRepository(session)

    with _patched_materialize():
        await repo.adopt_provider_food(
            "fatsecret",
            "33890",
            "Beef, ground",
            {"protein_100g": 26.0, "carbs_100g": 0.0, "fat_100g": 15.0},
            [{"name": "g", "grams": 1.0}],
            "en",
            "Beef, ground",
        )

    assert seed_row.name_normalized == "beef"


@pytest.mark.asyncio
async def test_adopt_verified_macros_failing_policy_raises_and_skips_verify():
    session = _AdoptSession(existing=None)
    repo = FoodReferenceAdoptRepository(session)

    with _patched_materialize():
        with pytest.raises(NutritionIntegrityError):
            await repo.adopt_provider_food(
                "fatsecret",
                "40000",
                "Impossible food",
                {"protein_100g": 100.0, "carbs_100g": 100.0, "fat_100g": 100.0},
                None,
                "en",
                "Impossible food",
            )

    new_model = session.added[0]
    assert new_model.is_verified is False


@pytest.mark.asyncio
async def test_adopt_writes_sanitized_name_vi():
    session = _AdoptSession(existing=None)
    repo = FoodReferenceAdoptRepository(session)

    with _patched_materialize():
        result = await repo.adopt_provider_food(
            "fatsecret",
            "33890",
            "Beef, ground, 85% lean",
            {"protein_100g": 26.0, "carbs_100g": 0.0, "fat_100g": 15.0},
            [{"name": "g", "grams": 1.0}],
            "vi",
            "  Th\u1eadt b\xf2\x00  xay  ",
        )

    assert result["locale"] == "vi"
    assert result["locale_name"] == "Th\u1eadt b\xf2 xay"
    assert session.added[0].name_vi == "Th\u1eadt b\xf2 xay"
    assert session.translation_statements == []


@pytest.mark.asyncio
async def test_adopt_identity_first_reuses_row_that_already_has_source_identity():
    existing = _existing_row(
        name="Beef, raw",
        name_normalized="beef raw",
        verified=True,
        source_namespace="fatsecret",
        source_food_id="33890",
    )
    session = _AdoptSession(existing=existing)
    repo = FoodReferenceAdoptRepository(session)

    with _patched_materialize():
        result = await repo.adopt_provider_food(
            "fatsecret",
            "33890",
            "Beef, raw",
            {"protein_100g": 1.0, "carbs_100g": 1.0, "fat_100g": 1.0},
            None,
            "en",
            "Beef, raw",
        )

    assert result["id"] == existing.id
    assert existing.name_normalized == "beef raw"
    assert session.added == []


@pytest.mark.asyncio
async def test_adopt_handles_concurrent_insert_race_via_savepoint_reread():
    winner = _existing_row(food_id=99, verified=False)
    session = _AdoptSession(existing=None, race_winner=winner)
    repo = FoodReferenceAdoptRepository(session)

    with _patched_materialize():
        result = await repo.adopt_provider_food(
            "fatsecret",
            "33890",
            "Beef, ground, 85% lean",
            {"protein_100g": 26.0, "carbs_100g": 0.0, "fat_100g": 15.0},
            [{"name": "g", "grams": 1.0}],
            "en",
            "Beef, ground, 85% lean",
        )

    assert result["id"] == 99
    assert len(session.added) == 1


@pytest.mark.asyncio
async def test_find_by_source_identity_returns_none_when_missing():
    session = _AdoptSession(existing=None)
    repo = FoodReferenceAdoptRepository(session)

    result = await repo.find_by_source_identity("fatsecret", "33890")

    assert result is None


@pytest.mark.asyncio
async def test_adopt_english_does_not_write_name_vi():
    existing = _existing_row(verified=True)
    session = _AdoptSession(existing=existing)
    repo = FoodReferenceAdoptRepository(session)

    with _patched_materialize():
        await repo.adopt_provider_food(
            "fatsecret",
            "33890",
            "Beef, raw",
            {"protein_100g": 26.0, "carbs_100g": 0.0, "fat_100g": 15.0},
            None,
            "en",
            "Beef, raw",
        )

    assert existing.name_vi is None
