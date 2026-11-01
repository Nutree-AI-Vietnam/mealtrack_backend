"""Video captions via yt-dlp, with oEmbed and page-tag fallbacks."""

from typing import Any

import httpx
import pytest

from src.domain.exceptions.recipe_import_exceptions import (
    RecipeImportError,
    RecipeImportFailure,
)
from src.domain.model.recipe_import import RecipeSourceMaterial, RecipeSourcePlatform
from src.infra.adapters.recipe_import.safe_fetch import SafeHttpFetcher
from src.infra.adapters.recipe_import.video_source_reader import VideoRecipeSourceReader
from src.infra.adapters.recipe_import.web_page_reader import WebRecipePageReader
from src.infra.adapters.recipe_import.ytdlp_support import (
    choose_subtitle_track,
    subtitle_text,
    video_error,
    ytdlp_options,
)

TIKTOK = RecipeSourcePlatform.TIKTOK
INFO = {
    "title": "Gà chiên nước mắm",
    "description": "Nguyên liệu: 500g cánh gà, 2 muỗng canh nước mắm",
    "uploader": "chef",
    "thumbnail": "https://p16.tiktokcdn.com/x.jpg",
    "duration": 45,
    "webpage_url": "https://www.tiktok.com/@chef/video/1",
    "subtitles": {
        "vie-VN": [{"ext": "vtt", "url": "https://v16.tiktokcdn.com/sub.vtt"}]
    },
}
VTT = b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nUop ga voi nuoc mam\n"


class FakeYdl:
    calls: list[dict[str, Any]] = []
    info: Any = INFO
    error: Exception | None = None
    # Errors raised by the first calls only, then the call succeeds.
    flakes: list[Exception] = []

    def __init__(self, options: dict[str, Any]) -> None:
        self.options = options
        FakeYdl.calls.append(options)

    def __enter__(self) -> "FakeYdl":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def extract_info(self, url: str, download: bool = False) -> Any:
        assert download is False
        if FakeYdl.flakes:
            raise FakeYdl.flakes.pop(0)
        if FakeYdl.error is not None:
            raise FakeYdl.error
        return FakeYdl.info

    @staticmethod
    def sanitize_info(info: dict[str, Any]) -> dict[str, Any]:
        return info


@pytest.fixture(autouse=True)
def _reset_fake(monkeypatch):
    from src.infra.adapters.recipe_import import ytdlp_support

    monkeypatch.setattr(ytdlp_support, "VIDEO_RETRY_BACKOFF_SECONDS", (0.0, 0.0))
    FakeYdl.calls = []
    FakeYdl.info = INFO
    FakeYdl.error = None
    FakeYdl.flakes = []


def _reader(handler) -> VideoRecipeSourceReader:
    async def resolver(host: str, port: int) -> list[str]:
        return ["93.184.215.14"]

    fetcher = SafeHttpFetcher(resolver=resolver, transport=httpx.MockTransport(handler))
    return VideoRecipeSourceReader(
        fetcher=fetcher,
        page_reader=WebRecipePageReader(fetcher),
        media_analyzer=None,
        ytdlp_factory=FakeYdl,
    )


@pytest.mark.asyncio
async def test_reads_caption_metadata_and_subtitles_without_downloading():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["host"] == "v16.tiktokcdn.com"
        return httpx.Response(200, headers={"content-type": "text/vtt"}, content=VTT)

    material = await _reader(handler).read_video(
        "https://vt.tiktok.com/ZS1/", TIKTOK, language="vi"
    )

    assert material.description == INFO["description"]
    assert material.canonical_url == "https://www.tiktok.com/@chef/video/1"
    assert (material.author, material.duration_seconds) == ("chef", 45)
    assert material.image_url == "https://p16.tiktokcdn.com/x.jpg"
    assert material.subtitles == "Uop ga voi nuoc mam"
    assert not material.metadata_only
    options = FakeYdl.calls[0]
    assert options["allowed_extractors"] == ["tiktok", r"vm\.tiktok"]
    assert options["noplaylist"] and options["skip_download"]


@pytest.mark.asyncio
async def test_playlist_posts_use_the_first_entry():
    FakeYdl.info = {
        "_type": "playlist",
        "description": "Carousel caption with the recipe",
        "entries": [
            {
                "title": "Part 1",
                "duration": 30,
                "webpage_url": "https://www.instagram.com/p/1/",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no subtitles to fetch")

    material = await _reader(handler).read_video(
        "https://www.instagram.com/p/1/", RecipeSourcePlatform.INSTAGRAM, language="en"
    )

    assert (material.title, material.description) == (
        "Part 1",
        "Carousel caption with the recipe",
    )


@pytest.mark.asyncio
async def test_blocked_video_falls_back_to_oembed_caption():
    FakeYdl.error = RuntimeError(
        "ERROR: Requested content is not available, login required"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oembed":
            return httpx.Response(
                200,
                json={
                    "title": "Full caption: 500g gà, 2 thìa nước mắm, chiên 10 phút",
                    "author_name": "chef",
                    "thumbnail_url": "https://p16/x.jpg",
                },
            )
        return httpx.Response(403)

    material = await _reader(handler).read_video(
        "https://www.tiktok.com/@chef/video/1", TIKTOK, language="vi"
    )

    assert material.metadata_only
    assert (
        material.description == "Full caption: 500g gà, 2 thìa nước mắm, chiên 10 phút"
    )
    assert (material.author, material.image_url) == ("chef", "https://p16/x.jpg")


@pytest.mark.asyncio
async def test_blocked_instagram_falls_back_to_page_tags():
    FakeYdl.error = RuntimeError("ERROR: login required")
    page = b'<html><head><meta property="og:description" content="Caption from the page"></head></html>'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=page)

    material = await _reader(handler).read_video(
        "https://www.instagram.com/reel/1/",
        RecipeSourcePlatform.INSTAGRAM,
        language="en",
    )

    assert FakeYdl.calls[0]["allowed_extractors"] == ["instagram"]
    assert material.description == "Caption from the page"
    assert material.metadata_only


@pytest.mark.asyncio
async def test_no_metadata_anywhere_raises_the_video_error():
    FakeYdl.error = RuntimeError("ERROR: Private video")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(RecipeImportError) as caught:
        await _reader(handler).read_video(
            "https://www.tiktok.com/@a/video/1", TIKTOK, language="en"
        )

    assert caught.value.failure == RecipeImportFailure.VIDEO_UNAVAILABLE


@pytest.mark.asyncio
async def test_media_analysis_is_skipped_without_an_analyzer():
    reader = _reader(lambda request: httpx.Response(404))
    material = RecipeSourceMaterial(
        platform=TIKTOK, source_url="https://www.tiktok.com/x"
    )

    analysis = await reader.analyze_media(material, language="en")

    assert analysis.is_empty and not reader.media_analysis_enabled


def test_errors_are_classified():
    assert (
        video_error(RuntimeError("Read timed out")).failure
        == RecipeImportFailure.SOURCE_UNREACHABLE
    )
    assert (
        video_error(RuntimeError("HTTP Error 503")).failure
        == RecipeImportFailure.SOURCE_UNREACHABLE
    )
    assert (
        video_error(RuntimeError("Sign in to confirm")).failure
        == RecipeImportFailure.VIDEO_UNAVAILABLE
    )


def test_options_only_enable_the_platform_extractors():
    options = ytdlp_options(RecipeSourcePlatform.YOUTUBE, socket_timeout=5)
    facebook = ytdlp_options(RecipeSourcePlatform.FACEBOOK, socket_timeout=5)

    assert options["allowed_extractors"] == ["youtube"]
    assert facebook["allowed_extractors"] == ["facebook", "facebook:reel"]
    assert "cookiefile" not in options
    assert options["cachedir"] is False


def test_subtitle_choice_prefers_manual_then_original_language_captions():
    info = {
        "language": "vi",
        "subtitles": {},
        "automatic_captions": {
            "ab": [{"ext": "vtt", "url": "u-ab"}],
            "vi-orig": [
                {"ext": "vtt", "url": "u-vtt"},
                {"ext": "json3", "url": "u-json"},
            ],
            "en": [{"ext": "vtt", "url": "u-en"}],
        },
    }
    manual = {"subtitles": {"en-US": [{"ext": "srv1", "url": "m-en"}]}}

    assert choose_subtitle_track(info, "en") == ("u-json", "json3")
    assert choose_subtitle_track(manual, "vi") == ("m-en", "srv1")
    assert choose_subtitle_track({"automatic_captions": {"ab": []}}, "vi") is None


def test_subtitle_text_drops_timing_tags_and_rolling_repeats():
    vtt = (
        "WEBVTT\nKind: captions\n\n00:00:00.000 --> 00:00:02.000 align:start\n"
        "first<00:00:01.000><c> add</c> onions\n\n00:00:02.000 --> 00:00:04.000\n"
        "first add onions\n\n3\n00:00:04.000 --> 00:00:06.000\nthen 2 cups rice\n"
    )
    json3 = '{"events":[{"segs":[{"utf8":"hello "},{"utf8":"world"}]},{"segs":[{"utf8":"hello world"}]}]}'

    assert subtitle_text(vtt, "vtt") == "first add onions\nthen 2 cups rice"
    assert subtitle_text(json3, "json3") == "hello world"
    assert subtitle_text("not json", "json3") == ""


@pytest.mark.asyncio
async def test_a_passing_bot_check_is_retried():
    FakeYdl.flakes = [
        RuntimeError("ERROR: [youtube] x: Sign in to confirm you're not a bot")
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/vtt"}, content=VTT)

    material = await _reader(handler).read_video(
        "https://www.tiktok.com/@chef/video/1", TIKTOK, language="vi"
    )

    assert len(FakeYdl.calls) == 2
    assert material.description == INFO["description"] and not material.metadata_only


@pytest.mark.asyncio
async def test_a_private_video_is_not_retried():
    FakeYdl.error = RuntimeError("ERROR: Private video")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(RecipeImportError):
        await _reader(handler).read_video(
            "https://www.tiktok.com/@a/video/1", TIKTOK, language="en"
        )

    assert len(FakeYdl.calls) == 1


def test_retryable_errors():
    from src.infra.adapters.recipe_import.ytdlp_support import is_retryable_video_error

    assert is_retryable_video_error(RuntimeError("Sign in to confirm you’re not a bot"))
    assert is_retryable_video_error(RuntimeError("HTTP Error 429: Too Many Requests"))
    assert is_retryable_video_error(RuntimeError("Read timed out"))
    assert not is_retryable_video_error(RuntimeError("Private video"))
    assert not is_retryable_video_error(
        RecipeImportError(RecipeImportFailure.VIDEO_UNAVAILABLE, "x")
    )
