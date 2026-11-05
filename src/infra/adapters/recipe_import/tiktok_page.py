"""The post TikTok embeds in its own video page.

TikTok answers yt-dlp from a server with a bot-check page, and its oEmbed
no longer carries the caption, so a TikTok link read neither way failed
with nothing to show. The page TikTok serves to an ordinary request still
embeds the post as JSON (``__UNIVERSAL_DATA_FOR_REHYDRATION__``): the
caption, author, cover and length, and the subtitle tracks its own player
loads, one of them TikTok's transcript of what is said. Reading that is
reading the page: nothing here answers a challenge or poses as a browser.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_DATA = re.compile(
    r'<script[^>]*\bid="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
    re.DOTALL,
)
# The desktop page and the mobile share ("reflow") page keep the post under
# different keys. The import's mobile user agent is served the second.
_DETAIL_KEYS = ("webapp.video-detail", "webapp.reflow.video.detail")
# TikTok names a track's language ISO 639-2 style ("vie-VN"); the app sends
# ISO 639-1 ("vi").
_TRACK_LANGUAGES = {
    "vi": "vie",
    "en": "eng",
    "de": "deu",
    "es": "spa",
    "fr": "fra",
    "zh": "cmn",
    "ja": "jpn",
    "ko": "kor",
    "th": "tha",
    "id": "ind",
}
# TikTok's transcript of the speech, in the language it was spoken. The
# other tracks ("MT") are machine translations of it.
_SPEECH = "ASR"
_WEBVTT = ("webvtt", "vtt")
_MAX_URL_CHARS = 2048


@dataclass(frozen=True, slots=True)
class TikTokSubtitle:
    url: str
    language: str
    source: str


@dataclass(frozen=True, slots=True)
class TikTokPost:
    caption: str | None
    author: str | None
    cover_url: str | None
    duration_seconds: int | None
    subtitles: tuple[TikTokSubtitle, ...] = ()


def is_tiktok_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == "tiktok.com" or host.endswith(".tiktok.com")


def without_query(url: str) -> str:
    """The post's address without the share-sheet tracking parameters."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def tiktok_post(html: str) -> TikTokPost | None:
    """The post a TikTok video page embeds, or None when it holds none."""
    match = _DATA.search(html)
    if match is None:
        return None
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return None
    scope = _dict(_dict(data).get("__DEFAULT_SCOPE__"))
    for key in _DETAIL_KEYS:
        detail = _dict(scope.get(key))
        # A removed or private post answers a non-zero status and no item.
        if detail.get("statusCode", 0) != 0:
            continue
        item = _dict(_dict(detail.get("itemInfo")).get("itemStruct"))
        if not item:
            continue
        video = _dict(item.get("video"))
        author = _dict(item.get("author"))
        return TikTokPost(
            caption=_text(item.get("desc")),
            author=_text(author.get("nickname")) or _text(author.get("uniqueId")),
            cover_url=_https(video.get("cover")) or _https(video.get("originCover")),
            duration_seconds=_seconds(video.get("duration")),
            subtitles=_subtitles(video.get("subtitleInfos")),
        )
    return None


def choose_tiktok_subtitle(post: TikTokPost, language: str) -> TikTokSubtitle | None:
    """TikTok's transcript of the speech first, then a track in the reader's
    language. A machine translation garbles the dish and ingredient names
    the recipe is written from; the recipe is translated afterwards anyway.
    """
    wanted = _TRACK_LANGUAGES.get(language.split("-")[0].lower(), "")
    ranked = sorted(
        post.subtitles,
        key=lambda track: (
            track.source.upper() != _SPEECH,
            not (wanted and track.language.lower().startswith(wanted)),
        ),
    )
    return ranked[0] if ranked else None


def _subtitles(raw: Any) -> tuple[TikTokSubtitle, ...]:
    tracks: list[TikTokSubtitle] = []
    for item in raw if isinstance(raw, list) else ():
        track = _dict(item)
        url = _https(track.get("Url"))
        if url is None or str(track.get("Format") or "").lower() not in _WEBVTT:
            continue
        tracks.append(
            TikTokSubtitle(
                url=url,
                language=str(track.get("LanguageCodeName") or ""),
                source=str(track.get("Source") or ""),
            )
        )
    return tuple(tracks)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _https(value: Any) -> str | None:
    if (
        isinstance(value, str)
        and value.startswith("https://")
        and len(value) <= _MAX_URL_CHARS
    ):
        return value
    return None


def _seconds(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if value > 0 else None
