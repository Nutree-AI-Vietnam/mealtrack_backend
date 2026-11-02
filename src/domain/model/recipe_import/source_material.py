"""What was collected from a recipe source before the AI reads it."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from src.domain.model.recipe_import.enums import (
    VIDEO_PLATFORMS,
    RecipeSignal,
    RecipeSourcePlatform,
)


@dataclass(frozen=True, slots=True)
class StructuredRecipe:
    """A schema.org ``Recipe`` found on a web page, flattened to text.

    Ingredients and steps stay as the page wrote them; the AI still reads
    them, because nutrition needs canonical names and gram weights.
    """

    name: str | None = None
    description: str | None = None
    ingredients: tuple[str, ...] = ()
    instructions: tuple[str, ...] = ()
    recipe_yield: str | None = None
    servings: int | None = None
    prep_time_minutes: int | None = None
    cook_time_minutes: int | None = None
    total_time_minutes: int | None = None
    image_url: str | None = None
    author: str | None = None
    cuisine: str | None = None
    category: str | None = None

    @property
    def is_complete(self) -> bool:
        """Enough to cook from without reading the rest of the page."""
        return len(self.ingredients) >= 2 and len(self.instructions) >= 1


@dataclass(frozen=True, slots=True)
class VideoMediaAnalysis:
    """What the video itself says and shows, beyond its caption."""

    transcript: str | None = None
    on_screen_text: str | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.transcript or self.on_screen_text)


@dataclass(frozen=True, slots=True)
class RecipeSourceMaterial:
    """Everything known about one source; every field is untrusted text."""

    platform: RecipeSourcePlatform
    source_url: str | None = None
    canonical_url: str | None = None
    title: str | None = None
    description: str | None = None
    author: str | None = None
    site_name: str | None = None
    image_url: str | None = None
    structured_recipe: StructuredRecipe | None = None
    page_text: str | None = None
    subtitles: str | None = None
    transcript: str | None = None
    on_screen_text: str | None = None
    user_text: str | None = None
    duration_seconds: int | None = None
    # Set when the video could not be read and only page metadata is known.
    metadata_only: bool = False
    # The page a video's caption links to, whose recipe ``structured_recipe``
    # then holds.
    linked_recipe_url: str | None = None
    notes: tuple[str, ...] = field(default=())

    @property
    def is_video(self) -> bool:
        return self.platform in VIDEO_PLATFORMS

    @property
    def has_text(self) -> bool:
        return any(
            (
                self.structured_recipe is not None,
                self.page_text,
                self.description,
                self.subtitles,
                self.transcript,
                self.on_screen_text,
                self.user_text,
            )
        )

    def with_linked_recipe(self, page: RecipeSourceMaterial) -> RecipeSourceMaterial:
        """Add the structured recipe of a page the caption links to."""
        return replace(
            self,
            structured_recipe=page.structured_recipe,
            linked_recipe_url=page.canonical_url or page.source_url,
            image_url=self.image_url or page.image_url,
        )

    def with_media(self, media: VideoMediaAnalysis) -> RecipeSourceMaterial:
        return replace(
            self,
            transcript=media.transcript or self.transcript,
            on_screen_text=media.on_screen_text or self.on_screen_text,
        )

    def signals(self) -> tuple[RecipeSignal, ...]:
        """Which kinds of text this material carries, in reading order."""
        found: list[RecipeSignal] = []
        if self.structured_recipe is not None:
            found.append(
                RecipeSignal.LINKED_RECIPE
                if self.linked_recipe_url
                else RecipeSignal.STRUCTURED_DATA
            )
        if self.page_text:
            found.append(RecipeSignal.PAGE_TEXT)
        if self.is_video and self.description:
            found.append(RecipeSignal.CAPTION)
        if self.subtitles:
            found.append(RecipeSignal.SUBTITLES)
        if self.transcript:
            found.append(RecipeSignal.SPEECH)
        if self.on_screen_text:
            found.append(RecipeSignal.ON_SCREEN_TEXT)
        if self.user_text:
            found.append(RecipeSignal.USER_TEXT)
        return tuple(found)
