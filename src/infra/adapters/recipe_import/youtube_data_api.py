"""A YouTube video's title and full description from the YouTube Data API.

Used only when YouTube refuses yt-dlp (its bot check on server IPs comes and
goes). The official API is not bot-checked, but gives no subtitles and never
the video itself, so the description is all the import can read.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from src.domain.model.recipe_import.enums import RecipeSourcePlatform
from src.domain.model.recipe_import.limits import (
    MAX_CAPTION_CHARS,
    MAX_LABEL_CHARS,
    MAX_NAME_CHARS,
)
from src.domain.model.recipe_import.source_material import RecipeSourceMaterial
from src.domain.model.recipe_import.text import clean_text

logger = logging.getLogger(__name__)

API_URL = "https://www.googleapis.com/youtube/v3/videos"
TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 512 * 1024
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_ISO_SECONDS = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+)S)?)?$"
)
_THUMBNAIL_SIZES = ("maxres", "standard", "high", "medium", "default")


class YouTubeDataApi:
    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._transport = transport

    async def read_video(self, video_id: str) -> RecipeSourceMaterial | None:
        """The video's text, or None when the API cannot give it."""
        if not _VIDEO_ID.match(video_id):
            return None
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    API_URL,
                    params={"part": "snippet,contentDetails", "id": video_id},
                    # A header keeps the key out of URLs and request logs.
                    headers={
                        "X-Goog-Api-Key": self._api_key,
                        "Accept": "application/json",
                    },
                )
        except httpx.HTTPError as exc:
            logger.warning("YouTube Data API unreachable: %s", type(exc).__name__)
            return None
        data = _json(response)
        if response.status_code != 200:
            logger.warning(
                "YouTube Data API refused: status=%d reason=%s",
                response.status_code,
                _error_reason(data),
            )
            return None
        items = data.get("items") if data else None
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            # Private, deleted or unknown video.
            return None
        return _material(items[0], video_id)


def _material(item: dict[str, Any], video_id: str) -> RecipeSourceMaterial | None:
    snippet = item.get("snippet")
    if not isinstance(snippet, dict):
        return None
    details = item.get("contentDetails")
    url = f"https://www.youtube.com/watch?v={video_id}"
    return RecipeSourceMaterial(
        platform=RecipeSourcePlatform.YOUTUBE,
        source_url=url,
        canonical_url=url,
        title=clean_text(snippet.get("title"), max_chars=MAX_NAME_CHARS),
        description=clean_text(
            snippet.get("description"), max_chars=MAX_CAPTION_CHARS, multiline=True
        ),
        author=clean_text(snippet.get("channelTitle"), max_chars=MAX_LABEL_CHARS),
        image_url=_thumbnail(snippet.get("thumbnails")),
        duration_seconds=_seconds(
            details.get("duration") if isinstance(details, dict) else None
        ),
        # The video itself was refused; only its text is known.
        metadata_only=True,
    )


def _json(response: httpx.Response) -> dict[str, Any] | None:
    if len(response.content) > MAX_RESPONSE_BYTES:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _error_reason(data: dict[str, Any] | None) -> str:
    error = (data or {}).get("error")
    if isinstance(error, dict):
        errors = error.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            return str(errors[0].get("reason") or "unknown")[:60]
        return str(error.get("status") or "unknown")[:60]
    return "unknown"


def _thumbnail(raw: Any) -> str | None:
    if not isinstance(raw, dict):
        return None
    for size in _THUMBNAIL_SIZES:
        entry = raw.get(size)
        url = entry.get("url") if isinstance(entry, dict) else None
        if isinstance(url, str) and url.startswith("https://") and len(url) <= 2048:
            return url
    return None


def _seconds(raw: Any) -> int | None:
    match = _ISO_SECONDS.match(raw) if isinstance(raw, str) else None
    if match is None:
        return None
    parts = {key: int(value or 0) for key, value in match.groupdict().items()}
    total = (
        parts["days"] * 86_400
        + parts["hours"] * 3_600
        + parts["minutes"] * 60
        + parts["seconds"]
    )
    return total or None
