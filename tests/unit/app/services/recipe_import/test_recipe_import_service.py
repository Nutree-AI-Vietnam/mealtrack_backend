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
async def test_a_blocked_tiktok_with_tiktok_s_transcript_still_gives_a_recipe(
    nutrition,
):
    """The caption only names the dish; the recipe is in what is said."""
    material = RecipeSourceMaterial(
        platform=RecipeSourcePlatform.TIKTOK,
        source_url="https://vt.tiktok.com/ZSp2P9MW7/",
        canonical_url="https://www.tiktok.com/@lenkycungkhoa/video/1",
        description="Công thức sườn nướng cơm tấm của mẹ tui",
        subtitles="mẹ tui ướp cho một kí sườn, đường muối bột ngọt tiêu, mật ong",
        metadata_only=True,
    )
    video = FakeVideo(material=material)
    extractor = FakeExtractor(results=[NOT_ENOUGH])

    recipe = await _service(
        video=video, extractor=extractor, nutrition=nutrition
    ).import_recipe(
        RecipeImportRequest(language="vi", url="https://vt.tiktok.com/ZSp2P9MW7/")
    )

    assert video.analyzed == 0
    assert extractor.materials[0].subtitles == material.subtitles
    assert recipe.ingredients
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


@pytest.mark.asyncio
async def test_a_page_that_makes_pieces_gets_servings_counted_in_people(nutrition):
    from src.domain.model.recipe_import.portions import PortionEstimate

    page = RecipeSourceMaterial(
        platform=RecipeSourcePlatform.WEBSITE,
        source_url="https://example.com/pancakes",
        structured_recipe=StructuredRecipe(
            name="Easy pancakes",
            ingredients=("100g flour", "2 eggs", "300ml milk"),
            instructions=("Whisk.", "Fry."),
            recipe_yield="Makes 12",
            servings=None,
        ),
    )
    # The reader copied "Makes 12" as 12 stated servings.
    extractor = FakeExtractor(
        results=[wings_recipe(servings=12, servings_stated=True)],
        portions=PortionEstimate(
            servings=4, lines=(), serving_description="3 bánh pancake"
        ),
    )

    recipe = await _service(
        web=FakeWeb(material=page), extractor=extractor, nutrition=nutrition
    ).import_recipe(
        RecipeImportRequest(language="vi", url="https://example.com/pancakes")
    )

    assert extractor.sized[0].servings_stated is False
    assert (recipe.servings, recipe.nutrition.servings) == (4, 4)
    assert recipe.servings_estimated
    assert RecipeImportWarning.SERVINGS_ESTIMATED in recipe.warnings
    assert (recipe.serving_description, recipe.makes) == ("3 bánh pancake", "Makes 12")
    assert recipe.nutrition.per_serving.protein == pytest.approx(
        recipe.nutrition.total.protein / 4, abs=0.1
    )


@pytest.mark.asyncio
async def test_stated_servings_are_not_estimated(nutrition):
    recipe = await _service(
        extractor=FakeExtractor(results=[wings_recipe()]), nutrition=nutrition
    ).import_recipe(
        RecipeImportRequest(
            language="vi", text="Cánh gà 500g, nước mắm 2 muỗng, chiên 12 phút"
        )
    )

    assert (recipe.servings, recipe.servings_estimated) == (2, False)
    assert RecipeImportWarning.SERVINGS_ESTIMATED not in recipe.warnings


BLOG_URL = "https://chef.blog/recipes/ga-chien/"
CAPTION_WITH_LINK = (
    "Cánh gà chiên nước mắm siêu giòn, ai cũng mê, làm cho cả nhà ăn cuối tuần "
    f"nhé. Công thức đầy đủ: {BLOG_URL}\u2063 #ganuocmam #anngon"
)
BLOG_PAGE = RecipeSourceMaterial(
    platform=RecipeSourcePlatform.WEBSITE,
    source_url=BLOG_URL,
    canonical_url=BLOG_URL,
    image_url="https://chef.blog/ga.jpg",
    structured_recipe=StructuredRecipe(
        name="Cánh gà chiên nước mắm",
        ingredients=("500 g cánh gà", "2 muỗng nước mắm", "3 tép tỏi"),
        instructions=("Ướp gà 15 phút.", "Chiên 12 phút."),
        recipe_yield="2 người",
        servings=2,
    ),
)


@pytest.mark.asyncio
async def test_a_caption_link_to_the_full_recipe_is_read_before_the_video(nutrition):
    video = FakeVideo(
        material=RecipeSourceMaterial(
            platform=RecipeSourcePlatform.INSTAGRAM,
            source_url="https://www.instagram.com/reel/abc/",
            description=CAPTION_WITH_LINK,
        )
    )
    web = FakeWeb(pages={BLOG_URL: BLOG_PAGE})
    extractor = FakeExtractor(results=[NOT_ENOUGH, wings_recipe()])

    recipe = await _service(
        web=web, video=video, extractor=extractor, nutrition=nutrition
    ).import_recipe(
        RecipeImportRequest(language="vi", url="https://www.instagram.com/reel/abc/")
    )

    assert web.urls == [BLOG_URL]
    assert video.analyzed == 0
    linked = extractor.materials[1]
    assert linked.structured_recipe == BLOG_PAGE.structured_recipe
    assert linked.description == CAPTION_WITH_LINK
    assert recipe.source.url == "https://www.instagram.com/reel/abc/"
    assert recipe.source.linked_recipe_url == BLOG_URL
    assert recipe.source.signals[:2] == (
        RecipeSignal.LINKED_RECIPE,
        RecipeSignal.CAPTION,
    )
    assert recipe.servings == 2 and not recipe.servings_estimated


@pytest.mark.asyncio
async def test_an_unreadable_caption_link_falls_back_to_the_video(nutrition):
    video = FakeVideo(
        material=RecipeSourceMaterial(
            platform=RecipeSourcePlatform.TIKTOK,
            source_url="https://www.tiktok.com/@chef/video/1",
            description=CAPTION_WITH_LINK,
        ),
        media=VideoMediaAnalysis(transcript="500 gam cánh gà, hai muỗng nước mắm"),
    )
    web = FakeWeb(
        pages={
            BLOG_URL: RecipeImportError(
                RecipeImportFailure.SOURCE_REJECTED, "403 from the blog"
            )
        }
    )
    extractor = FakeExtractor(results=[NOT_ENOUGH, wings_recipe()])

    recipe = await _service(
        web=web, video=video, extractor=extractor, nutrition=nutrition
    ).import_recipe(
        RecipeImportRequest(language="vi", url="https://www.tiktok.com/@chef/video/1")
    )

    assert web.urls == [BLOG_URL]
    assert video.analyzed == 1
    assert recipe.source.linked_recipe_url is None
    assert RecipeSignal.SPEECH in recipe.source.signals


@pytest.mark.asyncio
async def test_a_linked_page_without_a_full_recipe_is_ignored(nutrition):
    video = FakeVideo(
        material=RecipeSourceMaterial(
            platform=RecipeSourcePlatform.TIKTOK,
            source_url="https://www.tiktok.com/@chef/video/1",
            description=CAPTION_WITH_LINK,
        ),
        media=VideoMediaAnalysis(transcript="500 gam cánh gà"),
    )
    shop = RecipeSourceMaterial(
        platform=RecipeSourcePlatform.WEBSITE,
        source_url=BLOG_URL,
        structured_recipe=StructuredRecipe(name="Shop", ingredients=("pan",)),
    )
    extractor = FakeExtractor(results=[NOT_ENOUGH, wings_recipe()])

    await _service(
        web=FakeWeb(pages={BLOG_URL: shop}),
        video=video,
        extractor=extractor,
        nutrition=nutrition,
    ).import_recipe(
        RecipeImportRequest(language="vi", url="https://www.tiktok.com/@chef/video/1")
    )

    assert video.analyzed == 1
    assert all(m.linked_recipe_url is None for m in extractor.materials)


@pytest.mark.asyncio
async def test_a_blocked_video_still_reads_the_linked_recipe(nutrition):
    video = FakeVideo(
        material=RecipeSourceMaterial(
            platform=RecipeSourcePlatform.YOUTUBE,
            source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            description=CAPTION_WITH_LINK,
            metadata_only=True,
        )
    )
    extractor = FakeExtractor(results=[NOT_ENOUGH, wings_recipe()])

    recipe = await _service(
        web=FakeWeb(pages={BLOG_URL: BLOG_PAGE}),
        video=video,
        extractor=extractor,
        nutrition=nutrition,
    ).import_recipe(
        RecipeImportRequest(
            language="vi", url="https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
    )

    assert video.analyzed == 0
    assert recipe.source.linked_recipe_url == BLOG_URL
    assert RecipeImportWarning.VIDEO_NOT_ANALYZED not in recipe.warnings


@pytest.mark.asyncio
async def test_a_short_caption_with_a_link_goes_to_the_linked_page_first(nutrition):
    video = FakeVideo(
        material=RecipeSourceMaterial(
            platform=RecipeSourcePlatform.TIKTOK,
            source_url="https://www.tiktok.com/@chef/video/1",
            description=f"Gà chiên 😋 công thức: {BLOG_URL}",
        )
    )
    extractor = FakeExtractor(results=[wings_recipe()])

    recipe = await _service(
        web=FakeWeb(pages={BLOG_URL: BLOG_PAGE}),
        video=video,
        extractor=extractor,
        nutrition=nutrition,
    ).import_recipe(
        RecipeImportRequest(language="vi", url="https://www.tiktok.com/@chef/video/1")
    )

    assert len(extractor.materials) == 1 and video.analyzed == 0
    assert recipe.source.linked_recipe_url == BLOG_URL
