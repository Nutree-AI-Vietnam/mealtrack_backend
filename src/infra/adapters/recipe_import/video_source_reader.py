"""Read a recipe video's caption and subtitles without downloading it.

yt-dlp gives the caption, thumbnail, duration and subtitle tracks. When the
platform refuses yt-dlp, oEmbed (TikTok, YouTube) and the page's Open Graph
tags still carry the caption of most public posts.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Any
from urllib.parse import quote

from src.domain.exceptions.recipe_import_exceptions import RecipeImportError
from src.domain.model.recipe_import.enums import RecipeSourcePlatform
from src.domain.model.recipe_import.limits import (
    MAX_CAPTION_CHARS,
    MAX_LABEL_CHARS,
    MAX_NAME_CHARS,
    MAX_SUBTITLE_CHARS,
)
from src.domain.model.recipe_import.source_material import (
    RecipeSourceMaterial,
    VideoMediaAnalysis,
)
from src.domain.model.recipe_import.text import clean_text
from src.infra.adapters.recipe_import.safe_fetch import SafeHttpFetcher
from src.infra.adapters.recipe_import.video_media_analyzer import VideoMediaAnalyzer
from src.infra.adapters.recipe_import.web_page_reader import WebRecipePageReader
from src.infra.adapters.recipe_import.ytdlp_support import (
    choose_subtitle_track,
    subtitle_text,
    video_error,
    ytdlp_options,
)

logger = logging.getLogger(__name__)

MAX_SUBTITLE_BYTES = 2 * 1024 * 1024
_OEMBED = {
    RecipeSourcePlatform.TIKTOK: "https://www.tiktok.com/oembed?url={url}",
    RecipeSourcePlatform.YOUTUBE: "https://www.youtube.com/oembed?format=json&url={url}",
}


class VideoRecipeSourceReader:
    """``RecipeVideoSourcePort`` backed by yt-dlp, oEmbed and page metadata."""

    def __init__(
        self,
        *,
        fetcher: SafeHttpFetcher,
        page_reader: WebRecipePageReader,
        media_analyzer: VideoMediaAnalyzer | None,
        metadata_timeout_seconds: float = 30.0,
        ytdlp_factory: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._pages = page_reader
        self._media = media_analyzer
        self._timeout = metadata_timeout_seconds
        self._ytdlp_factory = ytdlp_factory or _default_ytdlp

    @property
    def media_analysis_enabled(self) -> bool:
        return self._media is not None and self._media.available

    async def read_video(
        self, url: str, platform: RecipeSourcePlatform, *, language: str
    ) -> RecipeSourceMaterial:
        try:
            info = await asyncio.wait_for(
                asyncio.to_thread(self._extract_info, url, platform), self._timeout
            )
        except (RecipeImportError, TimeoutError) as exc:
            failure = exc if isinstance(exc, RecipeImportError) else video_error(exc)
            logger.info(
                "Recipe video metadata unavailable: platform=%s code=%s",
                platform.value,
                failure.code,
            )
            fallback = await self._fallback(url, platform)
            if fallback is not None:
                return fallback
            raise failure from exc
        material = _material(info, url=url, platform=platform)
        subtitles = await self._subtitles(info, language)
        return replace(material, subtitles=subtitles) if subtitles else material

    async def analyze_media(
        self, material: RecipeSourceMaterial, *, language: str
    ) -> VideoMediaAnalysis:
        if not self.media_analysis_enabled or material.metadata_only:
            return VideoMediaAnalysis()
        assert self._media is not None
        url = material.canonical_url or material.source_url
        if not url:
            return VideoMediaAnalysis()
        return await self._media.analyze(
            url, material.platform, duration_seconds=material.duration_seconds
        )

    def _extract_info(self, url: str, platform: RecipeSourcePlatform) -> dict[str, Any]:
        options = ytdlp_options(
            platform,
            socket_timeout=min(self._timeout, 20.0),
            skip_download=True,
        )
        try:
            with self._ytdlp_factory(options) as ydl:
                info = ydl.extract_info(url, download=False)
                if isinstance(info, dict) and hasattr(ydl, "sanitize_info"):
                    # Materialise lazy playlist entries into plain lists.
                    info = ydl.sanitize_info(info)
        except Exception as exc:
            raise video_error(exc) from exc
        if not isinstance(info, dict):
            raise video_error(RuntimeError("no metadata"))
        return info

    async def _subtitles(self, info: dict[str, Any], language: str) -> str | None:
        track = choose_subtitle_track(_first_entry(info), language)
        if track is None:
            return None
        track_url, ext = track
        try:
            resource = await self._fetcher.get(
                track_url,
                max_bytes=MAX_SUBTITLE_BYTES,
                accept="*/*",
                allowed_types=("text/", "application/"),
                truncate_oversized=True,
            )
        except RecipeImportError as exc:
            logger.info("Recipe video subtitles skipped: code=%s", exc.code)
            return None
        return clean_text(
            subtitle_text(resource.text(), ext),
            max_chars=MAX_SUBTITLE_CHARS,
            multiline=True,
        )

    async def _fallback(
        self, url: str, platform: RecipeSourcePlatform
    ) -> RecipeSourceMaterial | None:
        oembed = await self._oembed(url, platform)
        try:
            page = await self._pages.read(url, platform=platform)
        except RecipeImportError:
            page = None
        title = (oembed or {}).get("title")
        caption = page.description if page else None
        # TikTok's oEmbed title is the full caption; prefer the longer text.
        if isinstance(title, str) and len(title) > len(caption or ""):
            caption = title
        if not caption and not (page and page.structured_recipe):
            return None
        return RecipeSourceMaterial(
            platform=platform,
            source_url=url,
            canonical_url=(page.canonical_url if page else None) or url,
            title=clean_text(title, max_chars=MAX_NAME_CHARS)
            or (page.title if page else None),
            description=clean_text(
                caption, max_chars=MAX_CAPTION_CHARS, multiline=True
            ),
            author=clean_text(
                (oembed or {}).get("author_name"), max_chars=MAX_LABEL_CHARS
            )
            or (page.author if page else None),
            image_url=_http_url((oembed or {}).get("thumbnail_url"))
            or (page.image_url if page else None),
            structured_recipe=page.structured_recipe if page else None,
            metadata_only=True,
        )

    async def _oembed(
        self, url: str, platform: RecipeSourcePlatform
    ) -> dict[str, Any] | None:
        template = _OEMBED.get(platform)
        if template is None:
            return None
        try:
            resource = await self._fetcher.get(
                template.format(url=quote(url, safe="")),
                max_bytes=256 * 1024,
                accept="application/json",
                allowed_types=("application/json", "text/"),
            )
            data = json.loads(resource.text())
        except (RecipeImportError, ValueError):
            return None
        return data if isinstance(data, dict) else None


def _material(
    info: dict[str, Any], *, url: str, platform: RecipeSourcePlatform
) -> RecipeSourceMaterial:
    entry = _first_entry(info)
    description = info.get("description") or entry.get("description")
    duration = entry.get("duration") or info.get("duration")
    return RecipeSourceMaterial(
        platform=platform,
        source_url=url,
        canonical_url=_http_url(entry.get("webpage_url") or info.get("webpage_url"))
        or url,
        title=clean_text(
            entry.get("title") or info.get("title"), max_chars=MAX_NAME_CHARS
        ),
        description=clean_text(
            description, max_chars=MAX_CAPTION_CHARS, multiline=True
        ),
        author=clean_text(
            entry.get("uploader") or entry.get("channel") or entry.get("creator"),
            max_chars=MAX_LABEL_CHARS,
        ),
        image_url=_http_url(entry.get("thumbnail") or info.get("thumbnail")),
        duration_seconds=int(duration)
        if isinstance(duration, (int, float)) and duration > 0
        else None,
    )


def _first_entry(info: dict[str, Any]) -> dict[str, Any]:
    if info.get("_type") == "playlist":
        for entry in info.get("entries") or []:
            if isinstance(entry, dict):
                return entry
    return info


def _http_url(value: Any) -> str | None:
    if (
        isinstance(value, str)
        and value.lower().startswith(("https://", "http://"))
        and len(value) <= 2048
    ):
        return value
    return None


def _default_ytdlp(options: dict[str, Any]) -> Any:
    from yt_dlp import YoutubeDL  # type: ignore[import-untyped]

    return YoutubeDL(options)
