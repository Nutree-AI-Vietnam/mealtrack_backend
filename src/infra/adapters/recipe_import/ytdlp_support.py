"""yt-dlp options, error mapping and subtitle parsing for recipe videos."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from src.domain.exceptions.recipe_import_exceptions import (
    RecipeImportError,
    RecipeImportFailure,
)
from src.domain.model.recipe_import.enums import RecipeSourcePlatform

# Only the platform's own extractors run. The generic extractor would follow
# links found inside a page, which a user-controlled post could point at an
# internal address.
_EXTRACTORS: dict[RecipeSourcePlatform, list[str]] = {
    RecipeSourcePlatform.YOUTUBE: ["youtube"],
    RecipeSourcePlatform.TIKTOK: ["tiktok", r"vm\.tiktok"],
    RecipeSourcePlatform.INSTAGRAM: ["instagram"],
    RecipeSourcePlatform.FACEBOOK: ["facebook", "facebook:reel"],
}
_TRANSIENT_MARKERS = (
    "timed out",
    "timeout",
    "temporary failure",
    "connection reset",
    "connection refused",
    "network is unreachable",
    "remote end closed",
    "http error 5",
)
_VTT_TIMING = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}\s+-->")
_TAG = re.compile(r"<[^>]{0,500}>")
SUBTITLE_FORMATS = ("json3", "vtt", "srv3", "srv2", "srv1", "ttml")


class _QuietLogger:
    def debug(self, message: str) -> None:
        return None

    def info(self, message: str) -> None:
        return None

    def warning(self, message: str) -> None:
        return None

    def error(self, message: str) -> None:
        return None


def ytdlp_options(
    platform: RecipeSourcePlatform,
    *,
    socket_timeout: float,
    cookies_file: str | None,
    **extra: Any,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "playlist_items": "1",
        "socket_timeout": socket_timeout,
        "retries": 1,
        "extractor_retries": 1,
        "fragment_retries": 1,
        "cachedir": False,
        "logger": _QuietLogger(),
        "allowed_extractors": _EXTRACTORS[platform],
    }
    if cookies_file and os.path.isfile(cookies_file):
        options["cookiefile"] = cookies_file
    options.update(extra)
    return options


def video_error(exc: BaseException) -> RecipeImportError:
    message = str(exc)
    lower = message.lower()
    if any(marker in lower for marker in _TRANSIENT_MARKERS):
        return RecipeImportError(
            RecipeImportFailure.SOURCE_UNREACHABLE,
            "The video platform is not responding right now.",
        )
    return RecipeImportError(
        RecipeImportFailure.VIDEO_UNAVAILABLE,
        "This video could not be opened. It may be private, need a login, "
        "or the platform refused the request.",
    )


def choose_subtitle_track(
    info: dict[str, Any], language: str
) -> tuple[str, str] | None:
    """``(url, ext)`` of the best subtitle track, manual before automatic."""
    original = str(info.get("language") or "").split("-")[0].lower()
    manual = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}
    preferred = [code for code in (language, original, "en") if code]
    for tracks, keys in (
        (manual, preferred),
        (automatic, [f"{original}-orig", *preferred] if original else preferred),
    ):
        if not isinstance(tracks, dict):
            continue
        for key in keys:
            choice = _track(tracks, key)
            if choice:
                return choice
    if isinstance(manual, dict):
        for key in manual:
            choice = _track(manual, key)
            if choice:
                return choice
    return None


def _track(tracks: dict[str, Any], key: str) -> tuple[str, str] | None:
    matches = [
        name
        for name in tracks
        if name.lower() == key.lower() or name.lower().startswith(f"{key.lower()}-")
    ]
    for name in matches:
        formats = tracks.get(name) or []
        by_ext = {
            str(item.get("ext")): str(item.get("url"))
            for item in formats
            if isinstance(item, dict) and item.get("url")
        }
        for ext in SUBTITLE_FORMATS:
            if ext in by_ext:
                return by_ext[ext], ext
    return None


def subtitle_text(body: str, ext: str) -> str:
    """Plain text from a subtitle file; rolling duplicates removed."""
    if ext == "json3":
        try:
            events = json.loads(body).get("events") or []
        except (ValueError, AttributeError):
            return ""
        lines = [
            "".join(
                str(seg.get("utf8", ""))
                for seg in event.get("segs") or []
                if isinstance(seg, dict)
            )
            for event in events
            if isinstance(event, dict)
        ]
    else:
        lines = []
        for raw in body.splitlines():
            line = raw.strip()
            if (
                not line
                or line == "WEBVTT"
                or line.isdigit()
                or _VTT_TIMING.search(line)
            ):
                continue
            if line.startswith(("NOTE", "Kind:", "Language:", "STYLE", "REGION")):
                continue
            lines.append(_TAG.sub("", line))
    cleaned: list[str] = []
    for line in lines:
        text = " ".join(line.split())
        if text and (not cleaned or text != cleaned[-1]):
            cleaned.append(text)
    return "\n".join(cleaned)
