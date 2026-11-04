"""Doses count as logged days for the streak, the recap and the day feed."""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.app.services.supplement_days import (
    dose_activities,
    dose_day,
    with_dose_dates,
    with_dose_days,
)
from src.domain.model.supplement import (
    ProductSource,
    ReviewStatus,
    SupplementForm,
    SupplementIntake,
    SupplementNutrients,
    SupplementProduct,
)

HANOI = ZoneInfo("Asia/Ho_Chi_Minh")
D1, D2, D3 = date(2026, 10, 30), date(2026, 10, 31), date(2026, 11, 1)


class _Repo:
    def __init__(self, ready=True, days=(), doses=(), products=None):
        self.ready, self.days, self.doses = ready, list(days), list(doses)
        self.products = products or {}

    async def schema_ready(self):
        return self.ready

    async def log_days(self, user_id):
        return self.days

    async def intakes_between(self, user_id, start, end):
        return [d for d in self.doses if start <= d.local_date <= end]

    async def products_for(self, user_id, ids):
        wanted = set(ids)
        return {k: v for k, v in self.products.items() if k in wanted}


def _uow(repo):
    return SimpleNamespace(supplements=repo)


def test_dose_day_uses_the_users_zone():
    # 18:30 UTC on 31 Oct is 01:30 on 1 Nov in Hanoi.
    taken = datetime(2026, 10, 31, 18, 30, tzinfo=UTC)
    now = datetime(2026, 11, 1, 2, 0, tzinfo=UTC)

    assert dose_day(taken, HANOI, now) == (D3, True)
    assert dose_day(datetime(2026, 10, 30, 3, 0, tzinfo=UTC), HANOI, now) == (D1, False)


@pytest.mark.asyncio
async def test_dose_days_join_meal_days_and_a_same_day_dose_marks_the_day():
    repo = _Repo(days=[(D3, True), (D2, False)])

    merged = await with_dose_days(_uow(repo), "u", [(D2, False), (D1, True)])

    assert merged == [(D3, True), (D2, False), (D1, True)]
    repo.days = [(D2, True)]
    assert await with_dose_days(_uow(repo), "u", [(D2, False)]) == [(D2, True)]


@pytest.mark.asyncio
async def test_recap_dates_include_dose_only_days():
    repo = _Repo(days=[(D3, True)])

    assert await with_dose_dates(_uow(repo), "u", [D1]) == [D3, D1]


@pytest.mark.asyncio
async def test_nothing_is_read_before_the_tables_exist():
    repo = _Repo(ready=False, days=[(D3, True)])

    assert await with_dose_days(_uow(repo), "u", [(D1, True)]) == [(D1, True)]
    assert await with_dose_dates(SimpleNamespace(), "u", [D1]) == [D1]
    assert await dose_activities(_uow(repo), "u", D3) == []


@pytest.mark.asyncio
async def test_the_day_feed_lists_doses_with_their_product():
    product = SupplementProduct(
        id="p1",
        name="Fish Oil 1000mg",
        brand="Blackmores",
        form=SupplementForm.SOFTGEL,
        unit_label="viên nang mềm",
        source=ProductSource.CURATED,
        review_status=ReviewStatus.APPROVED,
    )
    dose = SupplementIntake(
        id="i1",
        user_id="u",
        product_id="p1",
        units=2,
        taken_at=datetime(2026, 11, 1, 1, 0, tzinfo=UTC),
        local_date=D3,
        logged_on_day=True,
        nutrients=SupplementNutrients(),
    )
    repo = _Repo(doses=[dose], products={"p1": product})

    [row] = await dose_activities(_uow(repo), "u", D3)

    assert row["type"] == "supplement"
    assert row["title"] == "Fish Oil 1000mg" and row["units"] == 2
    assert row["unit_label"] == "viên nang mềm"
    assert row["timestamp"].startswith("2026-11-01T01:00:00")


@pytest.mark.asyncio
async def test_a_dose_whose_product_fails_to_load_fails_the_feed_rather_than_hiding_it():
    dose = SupplementIntake(
        id="i1",
        user_id="u",
        product_id="gone",
        units=1,
        taken_at=datetime(2026, 11, 1, 1, 0, tzinfo=UTC),
        local_date=D3,
        logged_on_day=True,
        nutrients=SupplementNutrients(),
    )

    with pytest.raises(LookupError):
        await dose_activities(_uow(_Repo(doses=[dose])), "u", D3)
