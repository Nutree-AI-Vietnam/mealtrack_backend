"""The + panel's Ghi nhanh handler on a transactional in-memory store.

Drinks, activities and the replay claim live in FakeDescribeDb, which applies
a unit of work only on a clean exit; the meal and dose writers are doubles
that write into the same unit of work. PostgreSQL behaviour is covered in
tests/integration/postgres/test_log_describe_postgres.py.
"""

from __future__ import annotations

import copy
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from tests.fixtures.fakes.fake_describe_db import FakeDescribeDb, FakeDescribeUow

from src.api.exceptions import ValidationException
from src.app.commands.describe_any import DescribeAnyCommand
from src.app.commands.meal.parse_meal_text_command import ParseMealTextCommand
from src.app.handlers.command_handlers.describe_any_command_handler import (
    DescribeAnyCommandHandler,
)
from src.app.handlers.command_handlers.supplement_command_handlers import SavedDose
from src.app.schemas.meal_schemas import ParsedFoodItemDto, ParseMealTextResponseDto
from src.app.services.describe_any_items import ALL_KINDS
from src.domain.exceptions.ai_exceptions import AIOutputValidationError
from src.domain.model.supplement import (
    ProductSource,
    ReviewStatus,
    SupplementForm,
    SupplementIntake,
    SupplementNutrients,
    SupplementProduct,
    UserSupplement,
)
from src.domain.utils.timezone_utils import utc_now

USER_ID = "55555555-5555-5555-5555-555555555555"
D3 = SupplementProduct(
    id="d3",
    name="Vitamin D3",
    form=SupplementForm.SOFTGEL,
    unit_label="viên nang mềm",
    source=ProductSource.CURATED,
    review_status=ReviewStatus.APPROVED,
    nutrients=SupplementNutrients({"vitamin_d": 25.0}),
)
WHEY = SupplementProduct(
    id="whey",
    name="Gold Whey",
    form=SupplementForm.POWDER,
    unit_label="muỗng",
    source=ProductSource.CURATED,
    review_status=ReviewStatus.APPROVED,
    macro_bearing=True,
)
SENTENCE = {
    "food": "phở bò",
    "drinks": [{"catalog_id": "tea", "name": "trà đá", "servings": 1}],
    "activities": [{"catalog_id": "running", "name": "chạy", "duration_min": 30}],
    "supplements": [{"name": "D3", "words": "1 viên D3", "units": 1, "list_ref": "s1"}],
}


class _Supplements:
    def __init__(self) -> None:
        self.listed = [UserSupplement("row-1", USER_ID, D3.id, units_per_dose=1)]
        self.catalog: list[SupplementProduct] = []
        self.added: list[SupplementProduct] = []

    async def list_supplements(self, user_id):
        return self.listed

    async def products_for(self, user_id, ids):
        return {D3.id: D3} if D3.id in ids else {}

    async def search_products(self, query, user_id, limit=20):
        return self.catalog[:limit]

    async def add_product(self, product):
        self.added.append(product)
        return ()

    async def intakes_between(self, user_id, start, end):
        return []


class _Db(FakeDescribeDb):
    def __init__(self) -> None:
        super().__init__()
        self.supplements = _Supplements()

    def uow(self) -> FakeDescribeUow:
        uow = super().uow()
        uow.supplements = self.supplements  # type: ignore[attr-defined]
        return uow


class _MealWriter:
    def __init__(self) -> None:
        self.commands: list = []
        self.publish_created = AsyncMock()

    async def save_prepared_meal(self, uow, command, received_at):
        self.commands.append(command)
        meal = SimpleNamespace(
            meal_id="meal-1",
            dish_name=command.dish_name,
            nutrition=SimpleNamespace(calories=389.25),
        )
        uow.work.meals.append(meal)
        return meal, date(2026, 10, 26)


class _DoseWriter:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._purge = AsyncMock()

    async def save_dose(self, uow, **kwargs) -> SavedDose:
        self.calls.append(kwargs)
        product = kwargs["product"]
        intake = SupplementIntake.log(
            id=kwargs["intake_id"],
            user_id=USER_ID,
            product=product,
            units=kwargs["units"] or 1,
            taken_at=kwargs["taken_at"],
            local_date=kwargs["taken_at"].date(),
            logged_on_day=True,
        )
        uow.work.entries.append(intake)
        entry = UserSupplement("row-1", USER_ID, product.id, units_per_dose=1)
        return SavedDose(entry, intake, "active")


class _Bus:
    def __init__(self) -> None:
        self.food_texts: list[str] = []

    async def send(self, command: Any):
        assert isinstance(command, ParseMealTextCommand)
        self.food_texts.append(command.text)
        item = ParsedFoodItemDto(
            name="Phở bò",
            quantity=450,
            unit="g",
            protein=27,
            carbs=45,
            fat=11.25,
            origin="custom",
            protein_per_100g=6.0,
            carbs_per_100g=10.0,
            fat_per_100g=2.5,
            calories_per_100g=86.5,
        )
        return ParseMealTextResponseDto(
            items=[item], total_protein=27, total_carbs=45, total_fat=11.25
        )


class _Env:
    def __init__(self, reply: dict = SENTENCE, *, supplements_on: bool = True):
        self.db = _Db()
        self.generation = SimpleNamespace(
            generate_meal_plan_async=AsyncMock(
                side_effect=lambda **_: copy.deepcopy(reply)
            )
        )
        self.bus = _Bus()
        self.meals = _MealWriter()
        self.doses = _DoseWriter()
        self.publisher = AsyncMock()
        self.handler = DescribeAnyCommandHandler(
            uow_factory=self.db.uow,
            meal_generation_service=self.generation,
            event_bus=self.bus,
            meal_writer=self.meals,  # type: ignore[arg-type]
            supplement_writer=self.doses,  # type: ignore[arg-type]
            supplements_available=AsyncMock(return_value=supplements_on),
            event_publisher=self.publisher,
        )

    async def send(self, key: str | None = "r-1", **fields: Any) -> dict:
        command = DescribeAnyCommand(
            user_id=USER_ID,
            text="phở bò, trà đá, chạy 30 phút, 1 viên D3",
            kinds=fields.pop("kinds", ALL_KINDS),
            idempotency_key=key,
            request_fingerprint="fp" if key else None,
            **fields,
        )
        return await self.handler.handle(command)

    @property
    def prompt(self) -> str:
        return self.generation.generate_meal_plan_async.await_args.kwargs["prompt"]


@pytest.mark.asyncio
async def test_four_kinds_save_together_then_publish_once_each():
    env = _Env()

    reply = await env.send()

    assert [item["kind"] for item in reply["saved"]] == [
        "food",
        "drink",
        "activity",
        "supplement",
    ]
    assert reply["skipped"] == [] and reply["nothing_found"] is False
    assert env.bus.food_texts == ["phở bò"]
    [meal] = env.meals.commands
    assert meal.source == "prompt" and meal.nutrition_contract_version == 2
    assert meal.items[0].custom_nutrition.protein_per_100g == 6.0
    # One unit of work holds the drink, the activity, the dose and the claim,
    # plus the meal and tea's legacy water meal row.
    assert len(env.db.state.entries) == 3 and len(env.db.state.meals) == 2
    assert env.db.claim("r-1").status == "completed"
    env.meals.publish_created.assert_awaited_once()
    assert env.publisher.publish.await_count == 2  # drink and activity events
    env.doses._purge.assert_awaited_once()
    assert env.doses.calls[0]["product"] is D3


@pytest.mark.asyncio
async def test_the_model_sees_the_list_as_data_on_the_openai_purpose():
    env = _Env()

    await env.send()

    kwargs = env.generation.generate_meal_plan_async.await_args.kwargs
    assert kwargs["model_purpose"] == "describe_log"
    assert env.prompt.startswith("<my_supplements>\n- s1: Vitamin D3 (viên nang mềm)")
    assert env.prompt.endswith("</sentence>")


@pytest.mark.asyncio
async def test_a_failed_commit_saves_and_publishes_nothing():
    env = _Env()
    env.db.fail_write_commit = "rolled_back"

    with pytest.raises(ConnectionError):
        await env.send()

    assert env.db.state.entries == [] and env.db.state.meals == []
    assert env.db.claim("r-1").status == "aborted"  # a retry runs fresh
    env.meals.publish_created.assert_not_awaited()
    env.publisher.publish.assert_not_awaited()
    env.doses._purge.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_retry_replays_without_a_second_model_call():
    env = _Env()

    first = await env.send()
    second = await env.send()

    assert second == first
    assert env.generation.generate_meal_plan_async.await_count == 1
    assert len(env.db.state.entries) == 3


@pytest.mark.asyncio
async def test_supplement_only_never_reads_the_food():
    env = _Env()

    reply = await env.send(kinds=frozenset({"supplement"}))

    assert [item["kind"] for item in reply["saved"]] == ["supplement"]
    assert env.bus.food_texts == [] and env.meals.commands == []
    assert {skip["kind"] for skip in reply["skipped"]} == {"food", "drink", "activity"}


@pytest.mark.asyncio
async def test_switched_off_supplements_are_skipped_and_the_list_is_not_sent():
    env = _Env(supplements_on=False)

    reply = await env.send()

    assert [item["kind"] for item in reply["saved"]] == ["food", "drink", "activity"]
    assert reply["skipped"][0]["reason"] == "supplements_unavailable"
    assert "my_supplements" not in env.prompt


@pytest.mark.asyncio
async def test_a_catalog_match_that_carries_calories_is_logged_as_food():
    sentence = {
        "supplements": [
            {"name": "whey", "words": "1 muỗng whey", "catalog_query": "Gold Whey"}
        ]
    }
    env = _Env(sentence)
    env.db.supplements.catalog = [WHEY]

    reply = await env.send()

    assert env.bus.food_texts == ["1 muỗng whey"]
    assert [item["kind"] for item in reply["saved"]] == ["food"]
    assert env.doses.calls == []


@pytest.mark.asyncio
async def test_an_unknown_supplement_is_saved_as_an_estimate():
    sentence = {
        "supplements": [
            {
                "name": "Kẽm",
                "words": "1 viên kẽm",
                "estimate": {
                    "form": "tablet",
                    "nutrients": [{"nutrient": "zinc", "amount": 15, "unit": "mg"}],
                },
            }
        ]
    }
    env = _Env(sentence)

    reply = await env.send()

    [product] = env.db.supplements.added
    assert product.estimated and product.owner_user_id == USER_ID
    [dose] = reply["saved"]
    assert dose["estimated"] is True and dose["intake"]["nutrients"] == {"zinc": 15.0}


@pytest.mark.asyncio
async def test_food_the_parser_cannot_read_is_skipped():
    env = _Env({"food": "món lạ"})
    env.bus.send = AsyncMock(  # type: ignore[method-assign]
        return_value=ParseMealTextResponseDto(
            items=[], total_protein=0, total_carbs=0, total_fat=0
        )
    )

    reply = await env.send()

    assert reply == {
        "saved": [],
        "skipped": [{"kind": "food", "title": "món lạ", "reason": "not_recognized"}],
        "nothing_found": False,
    }


@pytest.mark.asyncio
async def test_a_sentence_with_nothing_in_it_is_answered_and_stored():
    env = _Env({})

    reply = await env.send()

    assert reply == {"saved": [], "skipped": [], "nothing_found": True}
    assert env.db.claim("r-1").status == "completed"


@pytest.mark.asyncio
async def test_a_day_after_tomorrow_is_refused_before_the_model():
    env = _Env()

    with pytest.raises(ValidationException):
        await env.send(target_date=utc_now().date() + timedelta(days=3))

    env.generation.generate_meal_plan_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_publish_still_returns_what_was_saved():
    env = _Env()
    env.publisher.publish.side_effect = RuntimeError("queue down")
    env.meals.publish_created.side_effect = RuntimeError("queue down")

    reply = await env.send()

    assert len(reply["saved"]) == 4


@pytest.mark.asyncio
async def test_a_food_only_catalog_match_is_reported_on_the_supplement_card():
    sentence = {
        "supplements": [
            {"name": "whey", "words": "1 muỗng whey", "catalog_query": "Gold Whey"}
        ]
    }
    env = _Env(sentence)
    env.db.supplements.catalog = [WHEY]

    reply = await env.send(kinds=frozenset({"supplement"}))

    assert reply["saved"] == [] and reply["nothing_found"] is False
    assert reply["skipped"] == [
        {"kind": "food", "title": "whey", "reason": "not_requested"}
    ]


@pytest.mark.asyncio
async def test_a_list_product_the_person_can_no_longer_see_is_not_offered():
    env = _Env()
    withdrawn = SupplementProduct(
        id=D3.id,
        name=D3.name,
        form=D3.form,
        unit_label=D3.unit_label,
        source=ProductSource.CURATED,
        review_status=ReviewStatus.REJECTED,
    )
    env.db.supplements.products_for = AsyncMock(return_value={D3.id: withdrawn})

    await env.send()

    assert "my_supplements" not in env.prompt


@pytest.mark.asyncio
async def test_more_than_a_year_back_is_refused_before_the_model():
    env = _Env()

    with pytest.raises(ValidationException):
        await env.send(target_date=utc_now().date() - timedelta(days=400))

    env.generation.generate_meal_plan_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_today_sent_as_a_date_keeps_the_real_time():
    env = _Env()
    before = utc_now()

    reply = await env.send(target_date=before.date())  # the fake user is on UTC

    activity = next(i for i in reply["saved"] if i["kind"] == "activity")
    logged_at = activity["activity"]["logged_at"]
    assert logged_at.startswith(before.date().isoformat())
    assert env.meals.commands[0].target_date is None


@pytest.mark.asyncio
async def test_the_meal_is_published_under_its_own_day():
    env = _Env()

    await env.send()

    meal_day = env.meals.publish_created.await_args.args[1]
    assert meal_day == date(2026, 10, 26)  # what the meal writer saved it under


@pytest.mark.asyncio
async def test_an_unlisted_activity_without_a_met_is_asked_about_once_more():
    sentence = {
        "activities": [{"catalog_id": None, "name": "leo núi", "duration_min": 60}]
    }
    env = _Env(sentence)
    env.generation.generate_meal_plan_async.side_effect = [
        copy.deepcopy(sentence),
        {"activities": [{"ref": "a0", "met": 8.0}], "drinks": []},
    ]

    reply = await env.send()

    assert [item["kind"] for item in reply["saved"]] == ["activity"]
    [entry] = env.db.state.entries
    assert (entry.activity_id, entry.activity_name) == (None, "leo núi")
    second = env.generation.generate_meal_plan_async.await_args_list[1].kwargs
    assert second["model_purpose"] == "describe_log"
    assert second["prompt"] == "<sentence>\na0 | activity | leo núi |\n</sentence>"


@pytest.mark.asyncio
async def test_an_unlisted_drink_is_saved_even_when_the_second_read_fails():
    sentence = {"drinks": [{"catalog_id": None, "name": "nước lạ"}]}
    env = _Env(sentence)
    env.generation.generate_meal_plan_async.side_effect = [
        copy.deepcopy(sentence),
        ConnectionError("model down"),
    ]

    reply = await env.send()

    assert [item["kind"] for item in reply["saved"]] == ["drink"]
    [entry] = env.db.state.entries
    assert (entry.drink_id, entry.drink_name_snapshot) == ("other", "nước lạ")


@pytest.mark.asyncio
async def test_kinds_not_asked_for_are_never_estimated():
    env = _Env(
        {
            "activities": [{"catalog_id": None, "name": "leo núi"}],
            "drinks": [{"catalog_id": None, "name": "nước lạ"}],
        }
    )

    reply = await env.send(kinds=frozenset({"food"}))

    assert env.generation.generate_meal_plan_async.await_count == 1
    assert reply["saved"] == []
    assert {item["kind"] for item in reply["skipped"]} == {"activity", "drink"}


@pytest.mark.asyncio
async def test_food_is_read_once_more_when_its_estimate_is_unusable():
    env = _Env({"food": "phở bò"})
    read_food = env.bus.send
    texts: list[str] = []

    async def flaky(command: Any):
        texts.append(command.text)
        if len(texts) == 1:
            raise AIOutputValidationError(
                "bad portion", purpose="parse_text", attempt_count=1
            )
        return await read_food(command)

    env.bus.send = flaky  # type: ignore[method-assign]

    reply = await env.send()

    assert [item["kind"] for item in reply["saved"]] == ["food"]
    assert texts == ["phở bò", "phở bò"]


@pytest.mark.asyncio
async def test_food_unreadable_twice_is_skipped_and_the_rest_still_saves():
    env = _Env({"food": "món lạ", "drinks": [{"catalog_id": "tea", "name": "trà đá"}]})
    env.bus.send = AsyncMock(  # type: ignore[method-assign]
        side_effect=AIOutputValidationError(
            "bad portion", purpose="parse_text", attempt_count=1
        )
    )

    reply = await env.send()

    assert [item["kind"] for item in reply["saved"]] == ["drink"]
    assert reply["skipped"] == [
        {"kind": "food", "title": "món lạ", "reason": "not_recognized"}
    ]
    assert env.bus.send.await_count == 2
