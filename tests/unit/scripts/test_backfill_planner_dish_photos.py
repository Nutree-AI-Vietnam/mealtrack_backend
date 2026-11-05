"""Photographing AI-written plan dishes that were stored without a photo."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, lazyload

from src.domain.model.weekly_meal_planner.saved_recipe import SavedRecipeSnapshot
from src.infra.database.models.weekly_meal_planner.weekly_meal_plan_slot import (
    WeeklyMealPlanSlotORM,
)

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "backfill_planner_dish_photos.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "backfill_planner_dish_photos", _SCRIPT_PATH
)
assert _SPEC is not None
backfill = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(backfill)


class _Session:
    """Just enough of AsyncSession, over a real SQLite session."""

    def __init__(self, sync: Session):
        self.sync = sync

    async def execute(self, statement):
        return self.sync.execute(statement)

    async def flush(self):
        self.sync.flush()


class _Uow:
    def __init__(self, sync: Session, events: list[str]):
        self.session = _Session(sync)
        self._events = events

    async def __aenter__(self):
        self._events.append("open")
        return self

    async def __aexit__(self, exc_type, *_):
        if exc_type is None:
            self.session.sync.commit()
        else:
            self.session.sync.rollback()
        self._events.append("close")


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    WeeklyMealPlanSlotORM.__table__.create(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def events(db, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(backfill, "AsyncUnitOfWork", lambda: _Uow(db, seen))
    return seen


def _store_slot(db, slot_id, dish, *, slot_index=1):
    db.add(
        WeeklyMealPlanSlotORM(
            id=slot_id,
            plan_id="plan-1",
            day_index=0,
            slot_index=slot_index,
            saved_suggestion_id=dish.suggestion_id,
            saved_recipe=dish.to_dict(),
        )
    )
    db.commit()


def _dish(name, *, english=None, source="planner_ai", image_url=None):
    return SavedRecipeSnapshot(
        suggestion_id=f"ai_{name}",
        name=name,
        english_name=english,
        source=source,
        image_url=image_url,
    )


def _args(*, dry_run=False, plan_id=()):
    return SimpleNamespace(plan_id=list(plan_id), limit=50, dry_run=dry_run)


def _stored(db, slot_id):
    # Only the slot table exists here, so skip the catalog join a plain get
    # would make.
    db.expire_all()
    return db.get(
        WeeklyMealPlanSlotORM,
        slot_id,
        options=[lazyload(WeeklyMealPlanSlotORM.catalog_meal)],
    )


@pytest.mark.asyncio
async def test_only_ai_dishes_without_a_photo_are_picked(db):
    _store_slot(db, "s1", _dish("Cơm đậu phụ mè"), slot_index=0)
    _store_slot(db, "s2", _dish("Phở gà", image_url="https://p/pho"), slot_index=1)
    _store_slot(db, "s3", _dish("Bún chả", source="recipe_import"), slot_index=2)

    picked = await backfill._load_dishes(_Session(db), plan_ids=(), limit=10)
    elsewhere = await backfill._load_dishes(_Session(db), plan_ids=("p2",), limit=10)

    assert list(picked) == ["s1"]
    assert picked["s1"].name == "Cơm đậu phụ mè"
    assert elsewhere == {}


@pytest.mark.asyncio
async def test_each_dish_is_drawn_after_the_read_closes_and_then_stored(db, events):
    _store_slot(db, "s1", _dish("Bánh cuốn bơ trứng", english="Chicken Egg Wrap"))

    async def photographer(card):
        events.append("photo")
        return SimpleNamespace(url=f"https://photos.example/{card['english_name']}")

    summary = await backfill._run(_args(), photographer=photographer)

    assert summary == {"selected": 1, "updated": 1, "skipped": 0, "failed": 0}
    # No transaction is held open while a photo is being made.
    assert events[:3] == ["open", "close", "photo"]
    slot = _stored(db, "s1")
    assert slot.saved_recipe["image_url"] == "https://photos.example/Chicken Egg Wrap"
    assert slot.saved_recipe["name"] == "Bánh cuốn bơ trứng"
    assert slot.version == 1


@pytest.mark.asyncio
async def test_a_dish_swapped_meanwhile_is_left_alone(db, events):
    _store_slot(db, "s1", _dish("Cơm đậu phụ mè"))
    swapped_in = _dish("Cá hồi nướng")

    async def photographer(card):
        slot = _stored(db, "s1")
        slot.saved_suggestion_id = swapped_in.suggestion_id
        slot.saved_recipe = swapped_in.to_dict()
        db.commit()
        return SimpleNamespace(url="https://photos.example/tofu")

    summary = await backfill._run(_args(), photographer=photographer)

    assert summary["skipped"] == 1
    slot = _stored(db, "s1")
    assert slot.saved_recipe["name"] == "Cá hồi nướng"
    assert slot.saved_recipe["image_url"] is None


@pytest.mark.asyncio
async def test_a_photo_that_could_not_be_made_is_reported(db, events):
    _store_slot(db, "s1", _dish("Cơm đậu phụ mè"))

    async def photographer(card):
        return None

    summary = await backfill._run(_args(), photographer=photographer)

    assert summary == {"selected": 1, "updated": 0, "skipped": 0, "failed": 1}
    assert _stored(db, "s1").saved_recipe["image_url"] is None


@pytest.mark.asyncio
async def test_a_dry_run_draws_and_stores_nothing(db, events):
    _store_slot(db, "s1", _dish("Cơm đậu phụ mè"))

    async def photographer(card):  # pragma: no cover - must not be reached
        raise AssertionError("a dry run must not draw")

    summary = await backfill._run(_args(dry_run=True), photographer=photographer)

    assert summary == {"selected": 1, "updated": 0, "skipped": 0, "failed": 0}
    assert _stored(db, "s1").saved_recipe["image_url"] is None
