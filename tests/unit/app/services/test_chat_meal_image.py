from unittest.mock import AsyncMock

import pytest

from src.app.services.chat_meal_image import (
    ChatMealImageGenerator,
    build_chat_meal_image_prompt,
    chat_meal_image_id,
)
from src.app.services.chat_next_meal_candidates import ChatNextMealCandidates
from src.app.services.discovery_meal_images import attach_food_images
from src.domain.model.meal_discovery.food_image import FoodImageResult


def test_chat_photos_skip_cloudflare_when_unconfigured(monkeypatch) -> None:
    from src.api.dependencies import chat as chat_deps

    monkeypatch.setattr(chat_deps.settings, "CLOUDFLARE_ACCOUNT_ID", "")
    monkeypatch.setattr(chat_deps.settings, "CLOUDFLARE_API_TOKEN", "")
    assert chat_deps._chat_meal_image_generator() is None


def test_prompt_names_the_dish_and_forbids_text() -> None:
    prompt = build_chat_meal_image_prompt(
        {
            "name": "Chuối yến mạch và việt quất",
            "english_name": "Banana Oat Cup with Blueberries",
            "ingredients": [
                {"name": "banana", "amount": 100, "unit": "g"},
                {"name": "rolled oats", "amount": 40, "unit": "g"},
            ],
        }
    )
    assert "Banana Oat Cup with Blueberries" in prompt
    assert "banana" in prompt
    assert "rolled oats" in prompt
    assert "no text" in prompt
    assert "no logo" in prompt


def test_image_id_is_stable_for_the_same_dish() -> None:
    assert chat_meal_image_id("Banana Oat Cup") == chat_meal_image_id(
        "  banana oat cup "
    )
    assert chat_meal_image_id("Banana Oat Cup").startswith("chat-meals/")


@pytest.mark.asyncio
async def test_generator_reuses_a_stored_photo() -> None:
    generator = AsyncMock()
    images = ChatMealImageGenerator(
        generator,
        existing_image=AsyncMock(
            return_value="https://imagedelivery.net/acct/chat-meals/banana-oat-cup/public"
        ),
    )
    result = await images(
        {"english_name": "Banana Oat Cup", "ingredients": [{"name": "banana"}]}
    )
    assert result is not None
    assert result.source == "cloudflare"
    assert result.url.endswith("/public")
    generator.generate_url.assert_not_awaited()


@pytest.mark.asyncio
async def test_generator_uses_the_stored_photo_when_upload_collides() -> None:
    generator = AsyncMock()
    generator.generate_url.side_effect = RuntimeError("image already exists")
    stored = "https://imagedelivery.net/acct/chat-meals/banana-oat-cup/public"
    images = ChatMealImageGenerator(
        generator,
        existing_image=AsyncMock(side_effect=[None, stored]),
    )
    result = await images({"english_name": "Banana Oat Cup"})
    assert result is not None
    assert result.url == stored


@pytest.mark.asyncio
async def test_generator_stores_a_new_cloudflare_photo() -> None:
    generator = AsyncMock()
    generator.generate_url.return_value = (
        "https://imagedelivery.net/acct/chat-meals/banana-oat-cup/public"
    )
    images = ChatMealImageGenerator(
        generator, existing_image=AsyncMock(return_value=None)
    )
    meal = {
        "english_name": "Banana Oat Cup",
        "ingredients": [{"name": "banana"}],
    }
    result = await images(meal)
    assert result is not None
    assert result.thumbnail_url == result.url
    assert result.source == "cloudflare"
    generator.generate_url.assert_awaited_once()
    kwargs = generator.generate_url.await_args.kwargs
    assert kwargs["image_id"] == "chat-meals/banana-oat-cup"
    assert kwargs["output_format"] == "jpeg"
    assert "Banana Oat Cup" in generator.generate_url.await_args.args[0]


@pytest.mark.asyncio
async def test_attach_uses_generated_photo_and_skips_stock_search() -> None:
    async def generate(meal: dict) -> FoodImageResult:
        assert meal["english_name"] == "Banana Oat Cup"
        return FoodImageResult(
            url="https://imagedelivery.net/acct/chat-meals/banana/public",
            thumbnail_url="https://imagedelivery.net/acct/chat-meals/banana/public",
            source="cloudflare",
        )

    async def search(name: str):
        del name
        raise AssertionError("stock search should not run after a generated photo")

    meals = await attach_food_images(
        [{"name": "Chuối", "english_name": "Banana Oat Cup"}],
        search,
        generate_fn=generate,
    )
    assert meals[0]["image_url"].endswith("/public")
    assert meals[0]["thumbnail_url"] == meals[0]["image_url"]
    assert meals[0]["image_source"] == "cloudflare"


@pytest.mark.asyncio
async def test_attach_falls_back_to_stock_search_when_generation_fails() -> None:
    async def generate(meal: dict):
        del meal
        raise RuntimeError("workers ai down")

    async def search(name: str) -> FoodImageResult:
        assert name == "Banana Oat Cup"
        return FoodImageResult(
            url="https://images.pexels.com/banana.jpg",
            thumbnail_url="https://images.pexels.com/banana-sm.jpg",
            source="pexels",
        )

    meals = await attach_food_images(
        [{"name": "Chuối", "english_name": "Banana Oat Cup"}],
        search,
        generate_fn=generate,
    )
    assert meals[0]["image_source"] == "pexels"
    assert meals[0]["thumbnail_url"].endswith("banana-sm.jpg")


@pytest.mark.asyncio
async def test_next_meal_attach_images_generates_before_search() -> None:
    async def generate(meal: dict) -> FoodImageResult:
        del meal
        return FoodImageResult(
            url="https://imagedelivery.net/acct/chat-meals/tofu/public",
            thumbnail_url="https://imagedelivery.net/acct/chat-meals/tofu/public",
            source="cloudflare",
        )

    searched: list[str] = []

    async def search(name: str):
        searched.append(name)
        return None

    service = ChatNextMealCandidates(
        None,
        image_search=search,
        image_generator=generate,
    )
    cards = await service.attach_images(
        [{"name": "Tofu rice", "english_name": "Tofu rice"}]
    )
    assert cards[0]["image_source"] == "cloudflare"
    assert searched == []
