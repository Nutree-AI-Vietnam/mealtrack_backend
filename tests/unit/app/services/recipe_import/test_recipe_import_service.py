"""Orchestration of an import: which sources are read, and when."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from tests.fixtures.fakes.fake_recipe_import import (
    FakeExtractor,
    FakeVideo,
    FakeWeb,
    ingredient,
    wings_recipe,
)

from src.app.services.recipe_import.recipe_import_service import (
    RecipeImportRequest,
    RecipeImportService,
)
from src.app.services.recipe_import.recipe_nutrition_pricer import RecipeNutritionPricer
from src.domain.exceptions.recipe_import_exceptions import (
    RecipeImportError,
    RecipeImportFailure,
)
from src.domain.model.recipe_import import (
    ExtractedRecipe,
    RecipeImportSourceType,
    RecipeImportWarning,
    RecipeSignal,
    RecipeSourceMaterial,
    RecipeSourcePlatform,
    StructuredRecipe,
    VideoMediaAnalysis,
)

LONG_CAPTION = "Cánh gà chiên nước mắm: 500g cánh gà, 2 muỗng nước mắm, 3 tép tỏi. Ướp 15 phút rồi chiên."
TIKTOK_MATERIAL = RecipeSourceMaterial(
    platform=RecipeSourcePlatform.TIKTOK,
    source_url="https://vt.tiktok.com/ZS1/",
    canonical_url="https://www.tiktok.com/@chef/video/1",
    title="Gà chiên",
    description=LONG_CAPTION,
    author="chef",
    image_url="https://p16.tiktokcdn.com/thumb.jpg",
    duration_seconds=50,
)
NOT_ENOUGH = wings_recipe(
    quantities_stated=False,
    ingredients=(
        ingredient("cánh gà", "chicken wing, raw", None, None),
        ingredient("nước mắm", "fish sauce", None, None),
        ingredient("tỏi", "garlic", None, None),
    ),
)


def _service(*, web=None, video=None, extractor=None, nutrition, download=None):
    return RecipeImportService(
        web=web or FakeWeb(),
        video=video or FakeVideo(material=TIKTOK_MATERIAL),
        extractor=extractor or FakeExtractor(),
        pricer=RecipeNutritionPricer(nutrition),
        download_image=download or AsyncMock(),
        id_factory=lambda: "imp123456789",
    )


@pytest.mark.asyncio
async def test_web_page_draft_is_priced_and_attributed(nutrition):
    page = RecipeSourceMaterial(
        platform=RecipeSourcePlatform.WEBSITE,
        source_url="https://example.com/wings",
        canonical_url="https://example.com/wings",
        title="Wings",
        site_name="Bếp",
        image_url="https://example.com/wings.jpg",
        structured_recipe=StructuredRecipe(
            name="Wings",
            ingredients=("a", "b"),
            instructions=("c",),
            servings=4,
            cook_time_minutes=25,
        ),
        page_text="text",
    )
    web = FakeWeb(material=page)
    extractor = FakeExtractor(
        results=[wings_recipe(servings=None, servings_stated=False)]
    )

    recipe = await _service(
        web=web, extractor=extractor, nutrition=nutrition
    ).import_recipe(
        RecipeImportRequest(language="vi", url="Ngon nè https://example.com/wings#top")
    )

    assert web.urls == ["https://example.com/wings"]
    assert recipe.import_id == "imp123456789"
    assert recipe.servings == 4  # schema.org yield wins over the AI estimate
    assert recipe.cook_time_minutes == 25
    assert recipe.image_url == "https://example.com/wings.jpg"
    assert recipe.source.type == RecipeImportSourceType.URL
    assert recipe.source.signals == (
        RecipeSignal.STRUCTURED_DATA,
        RecipeSignal.PAGE_TEXT,
    )
    assert RecipeImportWarning.SERVINGS_ESTIMATED not in recipe.warnings
    assert RecipeImportWarning.NUTRITION_ESTIMATED in recipe.warnings  # garlic tier


@pytest.mark.asyncio
async def test_video_with_a_good_caption_is_not_downloaded(nutrition):
    video = FakeVideo(material=TIKTOK_MATERIAL)
    extractor = FakeExtractor(results=[wings_recipe()])

    recipe = await _service(
        video=video, extractor=extractor, nutrition=nutrition
    ).import_recipe(
        RecipeImportRequest(language="vi", url="https://vt.tiktok.com/ZS1/")
    )

    assert video.calls == [("https://vt.tiktok.com/ZS1/", RecipeSourcePlatform.TIKTOK)]
    assert video.analyzed == 0
    assert recipe.source.platform == RecipeSourcePlatform.TIKTOK
    assert recipe.source.url == "https://www.tiktok.com/@chef/video/1"
    assert recipe.warnings == (RecipeImportWarning.NUTRITION_ESTIMATED,)


@pytest.mark.asyncio
async def test_thin_caption_reads_the_video_itself(nutrition):
    video = FakeVideo(
        material=TIKTOK_MATERIAL,
        media=VideoMediaAnalysis(transcript="ướp gà", on_screen_text="500g cánh gà"),
    )
    extractor = FakeExtractor(results=[NOT_ENOUGH, wings_recipe()])

    recipe = await _service(
        video=video, extractor=extractor, nutrition=nutrition
    ).import_recipe(
        RecipeImportRequest(language="vi", url="https://vt.tiktok.com/ZS1/")
    )

    assert video.analyzed == 1
    second = extractor.materials[1]
    assert (second.transcript, second.on_screen_text) == ("ướp gà", "500g cánh gà")
    assert RecipeSignal.SPEECH in recipe.source.signals
    assert RecipeImportWarning.VIDEO_NOT_ANALYZED not in recipe.warnings


@pytest.mark.asyncio
async def test_short_caption_goes_straight_to_the_video(nutrition):
    material = RecipeSourceMaterial(
        platform=RecipeSourcePlatform.TIKTOK,
        source_url="https://www.tiktok.com/x",
        description="Yum 😋",
    )
    video = FakeVideo(
        material=material, media=VideoMediaAnalysis(transcript="recipe speech")
    )
    extractor = FakeExtractor(results=[wings_recipe()])

    await _service(video=video, extractor=extractor, nutrition=nutrition).import_recipe(
        RecipeImportRequest(language="vi", url="https://www.tiktok.com/x")
    )

    assert (
        len(extractor.materials) == 1
        and extractor.materials[0].transcript == "recipe speech"
    )


@pytest.mark.asyncio
async def test_failed_video_analysis_keeps_the_caption_draft_with_a_warning(nutrition):
    video = FakeVideo(
        material=TIKTOK_MATERIAL,
        media=RecipeImportError(RecipeImportFailure.VIDEO_UNAVAILABLE, "blocked"),
    )
    extractor = FakeExtractor(results=[NOT_ENOUGH])

    recipe = await _service(
        video=video, extractor=extractor, nutrition=nutrition
    ).import_recipe(
        RecipeImportRequest(language="vi", url="https://vt.tiktok.com/ZS1/")
    )

    assert RecipeImportWarning.VIDEO_NOT_ANALYZED in recipe.warnings
    assert RecipeImportWarning.QUANTITIES_ESTIMATED in recipe.warnings


@pytest.mark.asyncio
async def test_failed_analysis_without_any_recipe_reports_the_video_failure(nutrition):
    material = RecipeSourceMaterial(
        platform=RecipeSourcePlatform.TIKTOK,
        source_url="https://www.tiktok.com/x",
        description="Yum 😋",
    )
    video = FakeVideo(
        material=material,
        media=RecipeImportError(RecipeImportFailure.VIDEO_UNAVAILABLE, "blocked"),
    )
    extractor = FakeExtractor(
        results=[ExtractedRecipe(is_recipe=False, name="", language="vi")]
    )

    with pytest.raises(RecipeImportError) as caught:
        await _service(
            video=video, extractor=extractor, nutrition=nutrition
        ).import_recipe(
            RecipeImportRequest(language="vi", url="https://www.tiktok.com/x")
        )

    assert caught.value.failure == RecipeImportFailure.VIDEO_UNAVAILABLE


@pytest.mark.asyncio
async def test_metadata_only_videos_are_not_analyzed(nutrition):
    material = RecipeSourceMaterial(
        platform=RecipeSourcePlatform.INSTAGRAM,
        source_url="https://www.instagram.com/reel/1/",
        description=LONG_CAPTION,
        metadata_only=True,
    )
    video = FakeVideo(material=material)
    extractor = FakeExtractor(results=[NOT_ENOUGH])

    recipe = await _service(
        video=video, extractor=extractor, nutrition=nutrition
    ).import_recipe(
        RecipeImportRequest(language="vi", url="https://www.instagram.com/reel/1/")
    )

    assert video.analyzed == 0
    assert RecipeImportWarning.VIDEO_NOT_ANALYZED in recipe.warnings


@pytest.mark.asyncio
async def test_pasted_text_is_read_as_user_text(nutrition):
    extractor = FakeExtractor(results=[wings_recipe()])

    recipe = await _service(extractor=extractor, nutrition=nutrition).import_recipe(
        RecipeImportRequest(
            language="vi", text="Cánh gà 500g\nNước mắm 2 muỗng\nƯớp rồi chiên 12 phút"
        )
    )

    assert extractor.materials[0].platform == RecipeSourcePlatform.TEXT
    assert recipe.source.type == RecipeImportSourceType.TEXT
    assert recipe.source.signals == (RecipeSignal.USER_TEXT,)


@pytest.mark.asyncio
async def test_text_that_is_only_a_link_imports_the_link(nutrition):
    video = FakeVideo(material=TIKTOK_MATERIAL)
    extractor = FakeExtractor(results=[wings_recipe()])

    recipe = await _service(
        video=video, extractor=extractor, nutrition=nutrition
    ).import_recipe(
        RecipeImportRequest(language="vi", text="xem nè https://vt.tiktok.com/ZS1/")
    )

    assert video.calls and recipe.source.type == RecipeImportSourceType.URL


@pytest.mark.asyncio
async def test_too_short_text_is_rejected(nutrition):
    with pytest.raises(RecipeImportError) as caught:
        await _service(nutrition=nutrition).import_recipe(
            RecipeImportRequest(language="vi", text="phở")
        )

    assert caught.value.failure == RecipeImportFailure.INVALID_SOURCE


@pytest.mark.asyncio
async def test_photo_is_downloaded_compressed_and_read(nutrition):
    download = AsyncMock(return_value=b"raw-bytes")
    extractor = FakeExtractor(results=[wings_recipe()])

    recipe = await _service(
        extractor=extractor, nutrition=nutrition, download=download
    ).import_recipe(
        RecipeImportRequest(
            language="vi",
            image_url="https://imagedelivery.net/a/mealtrack/x/public",
            note="trang 12",
        )
    )

    download.assert_awaited_once_with("https://imagedelivery.net/a/mealtrack/x/public")
    assert extractor.photos == [b"raw-bytes"]  # not an image: compression keeps it
    assert recipe.source.type == RecipeImportSourceType.IMAGE
    assert recipe.image_url is None


@pytest.mark.asyncio
async def test_a_page_without_a_recipe_is_not_a_recipe(nutrition):
    page = RecipeSourceMaterial(
        platform=RecipeSourcePlatform.WEBSITE, page_text="About us"
    )
    extractor = FakeExtractor(
        results=[
            ExtractedRecipe(
                is_recipe=False, name="", language="en", rejection_reason="company page"
            )
        ]
    )

    with pytest.raises(RecipeImportError) as caught:
        await _service(
            web=FakeWeb(material=page), extractor=extractor, nutrition=nutrition
        ).import_recipe(
            RecipeImportRequest(language="en", url="https://example.com/about")
        )

    assert caught.value.failure == RecipeImportFailure.NOT_A_RECIPE
    assert caught.value.details == {"reason": "company page"}


@pytest.mark.asyncio
async def test_unsafe_links_are_refused_before_any_read(nutrition):
    web = FakeWeb()

    with pytest.raises(RecipeImportError) as caught:
        await _service(web=web, nutrition=nutrition).import_recipe(
            RecipeImportRequest(
                language="en", url="http://169.254.169.254/latest/meta-data"
            )
        )

    assert caught.value.failure == RecipeImportFailure.URL_NOT_ALLOWED
    assert web.urls == []


@pytest.mark.asyncio
async def test_the_portion_pass_sizes_lines_before_pricing(nutrition):
    from src.domain.model.recipe_import.portions import PortionEstimate, PortionLine

    sauce = wings_recipe(
        servings=None,
        servings_stated=False,
        quantities_stated=False,
        ingredients=(
            ingredient("cánh gà", "chicken wing, raw", None, None),
            ingredient("nước mắm", "fish sauce", 1, "cup"),
        ),
    )
    extractor = FakeExtractor(
        results=[sauce],
        portions=PortionEstimate(
            servings=4,
            lines=(
                PortionLine(0, 800.0, 800.0, "g", 1.0),
                PortionLine(1, 288.0, None, None, 0.25),
            ),
        ),
    )

    recipe = await _service(extractor=extractor, nutrition=nutrition).import_recipe(
        RecipeImportRequest(
            language="vi", text="Cánh gà chiên nước mắm, chấm kèm nước mắm pha"
        )
    )

    assert extractor.sized and extractor.sized[0] is not None
    assert [line["amount"] for line in nutrition.calls[0]] == [800.0, 72.0]
    wings, fish_sauce = recipe.ingredients
    assert (wings.quantity, wings.unit, wings.amount_estimated) == (800.0, "g", True)
    assert fish_sauce.eaten_share == 0.25
    assert recipe.servings == 4
    assert RecipeImportWarning.QUANTITIES_ESTIMATED in recipe.warnings


@pytest.mark.asyncio
async def test_a_failed_portion_pass_keeps_the_amounts_as_read(nutrition):
    extractor = FakeExtractor(
        results=[wings_recipe()], portions=RuntimeError("AI down")
    )

    recipe = await _service(extractor=extractor, nutrition=nutrition).import_recipe(
        RecipeImportRequest(
            language="vi", text="Cánh gà 500g, nước mắm 2 muỗng, chiên 12 phút"
        )
    )

    assert [item.grams for item in recipe.ingredients] == [500.0, 36.0, 9]


@pytest.mark.asyncio
async def test_a_refused_video_with_only_a_title_reports_the_refusal(nutrition):
    material = RecipeSourceMaterial(
        platform=RecipeSourcePlatform.YOUTUBE,
        source_url="https://www.youtube.com/watch?v=IANwP8_hwEk",
        description="A Better Way To Cook Pasta? | Techniquely with Lan Lam",
        metadata_only=True,
    )
    extractor = FakeExtractor(
        results=[ExtractedRecipe(is_recipe=False, name="", language="vi")]
    )

    with pytest.raises(RecipeImportError) as caught:
        await _service(
            video=FakeVideo(material=material), extractor=extractor, nutrition=nutrition
        ).import_recipe(
            RecipeImportRequest(
                language="vi", url="https://www.youtube.com/watch?v=IANwP8_hwEk"
            )
        )

    assert caught.value.failure == RecipeImportFailure.VIDEO_UNAVAILABLE
