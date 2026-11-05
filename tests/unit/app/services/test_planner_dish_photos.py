"""Photos for the dishes a model writes into a week."""

import asyncio
from types import SimpleNamespace

import pytest

from src.app.services.chat_meal_image import ChatMealImageGenerator
from src.app.services.planner_dish_photos import PlannerDishPhotos, meal_card
from src.domain.model.weekly_meal_planner.saved_recipe import (
    SavedRecipeIngredient,
    SavedRecipeSnapshot,
)


def _dish(name, *, english=None, lines=(), image_url=None):
    return SavedRecipeSnapshot(
        suggestion_id=f"ai_{name}",
        name=name,
        english_name=english,
        ingredients=tuple(SavedRecipeIngredient(name=line) for line in lines),
        image_url=image_url,
    )


class _Photographer:
    """Answers the way ChatMealImageGenerator does: a result with a url."""

    def __init__(self, *, fail=(), hold=(), answer_none=()):
        self.cards = []
        self.cancelled = []
        self._fail = set(fail)
        self._hold = set(hold)
        self._none = set(answer_none)
        self.release = asyncio.Event()

    async def __call__(self, card):
        self.cards.append(card)
        name = card["english_name"] or card["name"]
        if name in self._hold:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.append(name)
                raise
        if name in self._fail:
            raise RuntimeError("Cloudflare image generation returned 500")
        if name in self._none:
            return None
        return SimpleNamespace(url=f"https://photos.example/{name}")


@pytest.mark.asyncio
async def test_each_dish_gets_a_photo_drawn_from_what_was_cooked():
    """The reader's name drifted to "rice roll"; the photo must show a wrap."""
    photographer = _Photographer()
    photos = PlannerDishPhotos(photographer)
    wrap = _dish(
        "Bánh cuốn bơ trứng",
        english="Chicken Avocado Egg Wrap",
        lines=("whole-wheat tortilla", "chicken breast", "avocado"),
    )
    stew = _dish("Lentil tomato stew", lines=("lentils", "tomato"))

    photos.start(wrap)
    photos.start(stew)
    week = await photos.attach({(0, 0): wrap, (0, 1): stew})

    assert week[(0, 0)].image_url == "https://photos.example/Chicken Avocado Egg Wrap"
    assert week[(0, 1)].image_url == "https://photos.example/Lentil tomato stew"
    assert week[(0, 0)].name == "Bánh cuốn bơ trứng"
    first = photographer.cards[0]
    assert first["english_name"] == "Chicken Avocado Egg Wrap"
    assert [line["name"] for line in first["ingredients"]] == [
        "whole-wheat tortilla",
        "chicken breast",
        "avocado",
    ]


@pytest.mark.asyncio
async def test_two_dishes_drawn_alike_share_one_photo():
    photographer = _Photographer()
    photos = PlannerDishPhotos(photographer)
    monday = _dish("Cơm gà nướng", english="Grilled chicken rice")
    friday = _dish("Cơm gà nướng than", english="Grilled Chicken Rice")

    photos.start(monday)
    photos.start(friday)
    week = await photos.attach({(0, 1): monday, (4, 1): friday})

    assert len(photographer.cards) == 1
    assert week[(0, 1)].image_url == week[(4, 1)].image_url is not None


@pytest.mark.asyncio
async def test_a_photo_that_fails_leaves_only_its_own_dish_on_the_plate():
    photographer = _Photographer(fail={"Steamed fish"}, answer_none={"Plain congee"})
    photos = PlannerDishPhotos(photographer)
    fish, congee, salad = _dish("Steamed fish"), _dish("Plain congee"), _dish("Salad")

    for dish in (fish, congee, salad):
        photos.start(dish)
    week = await photos.attach({(0, 0): fish, (0, 1): congee, (0, 2): salad})

    assert week[(0, 0)].image_url is None
    assert week[(0, 1)].image_url is None
    assert week[(0, 2)].image_url == "https://photos.example/Salad"


@pytest.mark.asyncio
async def test_a_photo_still_being_made_does_not_hold_the_week():
    photographer = _Photographer(hold={"Slow soup"})
    photos = PlannerDishPhotos(photographer, settle_seconds=0.05)
    slow, quick = _dish("Slow soup"), _dish("Quick salad")

    photos.start(slow)
    photos.start(quick)
    week = await photos.attach({(0, 0): slow, (0, 1): quick})
    await asyncio.sleep(0)

    assert week[(0, 0)].image_url is None
    assert week[(0, 1)].image_url == "https://photos.example/Quick salad"
    assert photographer.cancelled == ["Slow soup"]


@pytest.mark.asyncio
async def test_a_week_that_fails_stops_its_photos():
    photographer = _Photographer(hold={"Slow soup"})
    photos = PlannerDishPhotos(photographer)

    photos.start(_dish("Slow soup"))
    await asyncio.sleep(0)
    photos.cancel()
    await asyncio.sleep(0)

    assert photographer.cancelled == ["Slow soup"]


@pytest.mark.asyncio
async def test_without_a_photographer_the_week_comes_back_as_it_was():
    photos = PlannerDishPhotos(None)
    dish = _dish("Cơm tấm")

    photos.start(dish)

    assert await photos.attach({(0, 1): dish}) == {(0, 1): dish}


@pytest.mark.asyncio
async def test_a_dish_that_has_a_photo_keeps_it():
    photographer = _Photographer()
    photos = PlannerDishPhotos(photographer)
    dish = _dish("Phở bò", image_url="https://photos.example/pho")

    photos.start(dish)
    week = await photos.attach({(0, 0): dish})

    assert photographer.cards == []
    assert week[(0, 0)].image_url == "https://photos.example/pho"


def test_the_card_skips_optional_lines():
    dish = SavedRecipeSnapshot(
        suggestion_id="ai_1",
        name="Cơm đậu phụ mè",
        english_name="Sesame Tofu Rice",
        ingredients=(
            SavedRecipeIngredient(name="firm tofu"),
            SavedRecipeIngredient(name="chili flakes", optional=True),
        ),
    )

    assert meal_card(dish) == {
        "name": "Cơm đậu phụ mè",
        "english_name": "Sesame Tofu Rice",
        "ingredients": [{"name": "firm tofu"}],
    }


@pytest.mark.asyncio
async def test_the_chat_photographer_draws_the_english_name_under_a_shared_id():
    """The planner reuses chat's photos: same prompt, same per-dish id."""
    asked = []

    class _Cloudflare:
        async def generate_url(self, prompt, *, image_id, **_):
            asked.append((prompt, image_id))
            return f"https://photos.example/{image_id}/public"

    async def nothing_stored(image_id):
        return None

    photos = PlannerDishPhotos(
        ChatMealImageGenerator(_Cloudflare(), existing_image=nothing_stored)
    )
    wrap = _dish(
        "Bánh cuốn bơ trứng",
        english="Chicken Avocado Egg Wrap",
        lines=("whole-wheat tortilla", "avocado"),
    )

    photos.start(wrap)
    week = await photos.attach({(0, 0): wrap})

    prompt, image_id = asked[0]
    assert image_id == "chat-meals/chicken-avocado-egg-wrap"
    assert "Chicken Avocado Egg Wrap" in prompt
    assert "whole-wheat tortilla" in prompt
    assert "Bánh cuốn" not in prompt
    assert week[(0, 0)].image_url == (
        "https://photos.example/chat-meals/chicken-avocado-egg-wrap/public"
    )


@pytest.mark.asyncio
async def test_a_dish_already_drawn_is_not_drawn_again():
    class _Cloudflare:
        async def generate_url(self, *_, **__):  # pragma: no cover - not reached
            raise AssertionError("a stored photo must be reused")

    async def stored(image_id):
        return f"https://photos.example/{image_id}/public"

    photos = PlannerDishPhotos(
        ChatMealImageGenerator(_Cloudflare(), existing_image=stored)
    )
    dish = _dish("Cơm đậu phụ mè", english="Sesame Tofu Rice")

    photos.start(dish)
    week = await photos.attach({(1, 2): dish})

    assert week[(1, 2)].image_url == (
        "https://photos.example/chat-meals/sesame-tofu-rice/public"
    )
