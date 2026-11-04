"""YouTube Data API: the description of a bot-checked video, key kept out of URLs."""

import json

import httpx
import pytest

from src.domain.model.recipe_import import RecipeSourcePlatform
from src.infra.adapters.recipe_import.youtube_data_api import YouTubeDataApi

VIDEO = {
    "items": [
        {
            "id": "zMUNG9KXmrE",
            "snippet": {
                "title": "Traditional Carbonara",
                "description": "Ingredients -\n400g (14.1oz) - Spaghetti\n4 - Egg Yolks",
                "channelTitle": "Chef Jack Ovens",
                "thumbnails": {
                    "default": {
                        "url": "https://i.ytimg.com/vi/zMUNG9KXmrE/default.jpg"
                    },
                    "high": {"url": "https://i.ytimg.com/vi/zMUNG9KXmrE/hqdefault.jpg"},
                },
            },
            "contentDetails": {"duration": "PT1M2S"},
        }
    ]
}


def _api(handler) -> YouTubeDataApi:
    return YouTubeDataApi(api_key="test-key", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_reads_title_description_channel_thumbnail_and_duration():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=VIDEO)

    material = await _api(handler).read_video("zMUNG9KXmrE")

    (request,) = requests
    assert request.url.host == "www.googleapis.com"
    assert request.url.path == "/youtube/v3/videos"
    assert dict(request.url.params) == {
        "part": "snippet,contentDetails",
        "id": "zMUNG9KXmrE",
    }
    assert request.headers["X-Goog-Api-Key"] == "test-key"
    assert "test-key" not in str(request.url)
    assert material is not None
    assert material.platform == RecipeSourcePlatform.YOUTUBE
    assert material.canonical_url == "https://www.youtube.com/watch?v=zMUNG9KXmrE"
    assert material.title == "Traditional Carbonara"
    assert material.description == (
        "Ingredients -\n400g (14.1oz) - Spaghetti\n4 - Egg Yolks"
    )
    assert material.author == "Chef Jack Ovens"
    assert material.image_url == "https://i.ytimg.com/vi/zMUNG9KXmrE/hqdefault.jpg"
    assert material.duration_seconds == 62
    assert material.metadata_only


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"items": []}),
        httpx.Response(
            403,
            json={"error": {"errors": [{"reason": "quotaExceeded"}], "code": 403}},
        ),
        httpx.Response(400, content=b"not json"),
        httpx.Response(200, content=json.dumps({"items": [{"id": "x"}]}).encode()),
    ],
)
async def test_unknown_videos_and_api_errors_give_nothing(response):
    material = await _api(lambda request: response).read_video("zMUNG9KXmrE")

    assert material is None


@pytest.mark.asyncio
async def test_network_failures_give_nothing():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    assert await _api(handler).read_video("zMUNG9KXmrE") is None


@pytest.mark.asyncio
async def test_malformed_video_ids_are_not_sent():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    assert await _api(handler).read_video("../../channels") is None
