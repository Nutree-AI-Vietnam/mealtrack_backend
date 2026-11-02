"""Import a recipe from a link, pasted text or a photo into a draft.

Web pages are read once. Videos are read caption-first; when the caption
does not hold a usable recipe, a full-recipe page it links to is read next,
and the video itself (speech and on-screen text) is downloaded last. The
draft is priced by the backend nutrition lookup and nothing is saved until
the user saves it.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from src.app.ports.image_download_port import ImageDownloadPort
from src.app.services.recipe_import.imported_recipe_builder import (
    build_imported_recipe,
)
from src.app.services.recipe_import.recipe_nutrition_pricer import (
    RecipeNutritionPricer,
)
from src.domain.exceptions.recipe_import_exceptions import (
    RecipeImportError,
    RecipeImportFailure,
)
from src.domain.model.recipe_import.enums import (
    VIDEO_PLATFORMS,
    RecipeImportSourceType,
    RecipeImportWarning,
    RecipeSourcePlatform,
)
from src.domain.model.recipe_import.extracted_recipe import ExtractedRecipe
from src.domain.model.recipe_import.imported_recipe import ImportedRecipe
from src.domain.model.recipe_import.limits import (
    MAX_RECIPE_TEXT_LENGTH,
    MIN_RECIPE_TEXT_LENGTH,
)
from src.domain.model.recipe_import.link_policy import (
    caption_recipe_links,
    classify_recipe_url,
    extract_recipe_url,
)
from src.domain.model.recipe_import.portions import apply_portion_estimate
from src.domain.model.recipe_import.quality import (
    caption_is_enough,
    has_substantial_caption,
    require_recipe,
)
from src.domain.model.recipe_import.servings import with_source_servings
from src.domain.model.recipe_import.source_material import RecipeSourceMaterial
from src.domain.model.recipe_import.text import clean_text
from src.domain.ports.recipe_import_ports import (
    RecipeExtractorPort,
    RecipeVideoSourcePort,
    RecipeWebPagePort,
)
from src.domain.utils.image_compression import compress_vision_image

logger = logging.getLogger(__name__)

# Pasted text that is little more than a link is imported as that link.
_LINK_ONLY_SLACK = 40
_URL = re.compile(r"https?://\S+", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class RecipeImportRequest:
    language: str
    url: str | None = None
    text: str | None = None
    image_url: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class _Read:
    material: RecipeSourceMaterial
    extracted: ExtractedRecipe
    source_type: RecipeImportSourceType
    warnings: tuple[RecipeImportWarning, ...] = ()


class RecipeImportService:
    def __init__(
        self,
        *,
        web: RecipeWebPagePort,
        video: RecipeVideoSourcePort,
        extractor: RecipeExtractorPort,
        pricer: RecipeNutritionPricer,
        download_image: ImageDownloadPort,
        id_factory: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._web = web
        self._video = video
        self._extractor = extractor
        self._pricer = pricer
        self._download_image = download_image
        self._id_factory = id_factory

    async def import_recipe(self, request: RecipeImportRequest) -> ImportedRecipe:
        read = await self._read(request)
        extracted = await self._size(
            with_source_servings(
                require_recipe(read.extracted), read.material.structured_recipe
            )
        )
        priced = await self._pricer.price(
            extracted.ingredients, servings=extracted.servings
        )
        return build_imported_recipe(
            import_id=self._id_factory(),
            extracted=extracted,
            priced=priced,
            material=read.material,
            source_type=read.source_type,
            warnings=read.warnings,
        )

    async def _size(self, recipe: ExtractedRecipe) -> ExtractedRecipe:
        """Fill missing amounts, eaten shares and servings in a second AI pass.

        A failed pass keeps the amounts as read: the draft is still useful
        and the review screen shows which lines have no nutrition.
        """
        try:
            estimate = await self._extractor.estimate_portions(recipe)
        except Exception as exc:  # noqa: BLE001 - sizing is an improvement only
            logger.warning("Recipe portion pass failed: %s", type(exc).__name__)
            return recipe
        return apply_portion_estimate(recipe, estimate)

    async def _read(self, request: RecipeImportRequest) -> _Read:
        if request.url:
            return await self._from_url(request.url, request.language)
        if request.text:
            link = _link_only(request.text)
            if link is not None:
                return await self._from_url(link, request.language)
            return await self._from_text(request.text, request.language)
        if request.image_url:
            return await self._from_photo(
                request.image_url, request.language, request.note
            )
        raise RecipeImportError(
            RecipeImportFailure.INVALID_SOURCE, "Send a link, some text or a photo."
        )

    async def _from_url(self, raw_url: str, language: str) -> _Read:
        url = extract_recipe_url(raw_url)
        platform, fetch_url = classify_recipe_url(url)
        if platform in VIDEO_PLATFORMS:
            return await self._from_video(fetch_url, platform, language)
        material = await self._web.read_page(fetch_url)
        if not material.has_text:
            raise RecipeImportError(
                RecipeImportFailure.NOT_A_RECIPE, "The page has no readable text."
            )
        extracted = await self._extractor.extract(material, language=language)
        return _Read(material, extracted, RecipeImportSourceType.URL)

    async def _from_video(
        self, url: str, platform: RecipeSourcePlatform, language: str
    ) -> _Read:
        material = await self._video.read_video(url, platform, language=language)
        can_analyze = self._video.media_analysis_enabled and not material.metadata_only
        extracted: ExtractedRecipe | None = None
        if material.has_text and (has_substantial_caption(material) or not can_analyze):
            extracted = await self._extractor.extract(material, language=language)
            if caption_is_enough(extracted):
                return _Read(material, extracted, RecipeImportSourceType.URL)
        linked = await self._linked_recipe(material)
        if linked is not None:
            material = linked
            extracted = await self._extractor.extract(material, language=language)
            if caption_is_enough(extracted):
                return _Read(material, extracted, RecipeImportSourceType.URL)
        analysis_error: RecipeImportError | None = None
        if can_analyze:
            try:
                media = await self._video.analyze_media(material, language=language)
            except RecipeImportError as exc:
                logger.info("Recipe video not analyzed: code=%s", exc.code)
                analysis_error = exc
            else:
                if not media.is_empty:
                    analyzed = material.with_media(media)
                    extracted = await self._extractor.extract(
                        analyzed, language=language
                    )
                    return _Read(analyzed, extracted, RecipeImportSourceType.URL)
        if extracted is None and material.has_text:
            extracted = await self._extractor.extract(material, language=language)
        if extracted is None or not (extracted.is_recipe and extracted.ingredients):
            if analysis_error is not None:
                raise analysis_error
            if material.metadata_only:
                # The platform refused the video (often its bot check on
                # server IPs); a title alone says nothing about the recipe.
                raise RecipeImportError(
                    RecipeImportFailure.VIDEO_UNAVAILABLE,
                    "The platform did not let us read this video. Try again "
                    "later, or paste the recipe text instead.",
                )
            raise RecipeImportError(
                RecipeImportFailure.NOT_A_RECIPE,
                "No recipe was found in this video's caption or speech.",
            )
        return _Read(
            material,
            extracted,
            RecipeImportSourceType.URL,
            (RecipeImportWarning.VIDEO_NOT_ANALYZED,),
        )

    async def _linked_recipe(
        self, material: RecipeSourceMaterial
    ) -> RecipeSourceMaterial | None:
        """The material plus the recipe of a page its caption links to.

        Only a page with a complete schema.org recipe counts; any failure
        just means the video is read instead.
        """
        if material.structured_recipe is not None:
            return None
        links = caption_recipe_links(material.description)
        if not links:
            return None
        pages = await asyncio.gather(*(self._linked_page(link) for link in links))
        for page in pages:
            if (
                page is not None
                and page.structured_recipe is not None
                and page.structured_recipe.is_complete
            ):
                return material.with_linked_recipe(page)
        return None

    async def _linked_page(self, url: str) -> RecipeSourceMaterial | None:
        try:
            return await self._web.read_page(url)
        except Exception as exc:  # noqa: BLE001 - the video is read instead
            logger.info("Caption recipe link skipped: %s", type(exc).__name__)
            return None

    async def _from_text(self, text: str, language: str) -> _Read:
        cleaned = clean_text(text, max_chars=MAX_RECIPE_TEXT_LENGTH, multiline=True)
        if not cleaned or len(cleaned) < MIN_RECIPE_TEXT_LENGTH:
            raise RecipeImportError(
                RecipeImportFailure.INVALID_SOURCE,
                "The text is too short to hold a recipe.",
            )
        material = RecipeSourceMaterial(
            platform=RecipeSourcePlatform.TEXT, user_text=cleaned
        )
        extracted = await self._extractor.extract(material, language=language)
        return _Read(material, extracted, RecipeImportSourceType.TEXT)

    async def _from_photo(
        self, image_url: str, language: str, note: str | None
    ) -> _Read:
        raw = await self._download_image(image_url)
        image = await asyncio.to_thread(compress_vision_image, raw)
        extracted = await self._extractor.read_photo(
            image, language=language, note=clean_text(note, max_chars=300)
        )
        material = RecipeSourceMaterial(platform=RecipeSourcePlatform.IMAGE)
        return _Read(material, extracted, RecipeImportSourceType.IMAGE)


def _link_only(text: str) -> str | None:
    match = _URL.search(text)
    if match is None:
        return None
    rest = (text[: match.start()] + text[match.end() :]).strip()
    return match.group(0) if len(rest) <= _LINK_ONLY_SLACK else None
