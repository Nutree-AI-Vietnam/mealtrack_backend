"""Unit tests for ScanByUrlCommandHandler image download resilience and fallback."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.api.exceptions import (
    ExternalServiceException,
    ResourceNotFoundException,
    handle_exception,
)
from src.app.commands.meal.scan_by_url_command import ScanByUrlCommand
from src.app.handlers.command_handlers.scan_by_url_command_handler import (
    ScanByUrlCommandHandler,
)
from src.domain.parsers.vision_response_parser import VisionResponseParser
from src.infra.http import image_download

_USER_ID = "00000000-0000-0000-0000-000000000001"
_IMAGE_ID = "1325c7ca-e012-4df3-b0b4-55bfaeb55eb0"
_CF_IMAGE_URL = f"https://imagedelivery.net/PeQb0oPRIbwHNu4iebuEpQ/{_IMAGE_ID}/public"
_PUBLIC_ID = f"PeQb0oPRIbwHNu4iebuEpQ/{_IMAGE_ID}"


def _make_uow() -> MagicMock:
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.users = MagicMock()
    uow.users.get_user_timezone = AsyncMock(return_value="UTC")
    uow.meals = MagicMock()
    saved_meals = []

    async def capture_meal(meal):
        saved_meals.append(meal)
        return meal

    uow.meals.save = AsyncMock(side_effect=capture_meal)
    uow.meals.find_by_id = AsyncMock(side_effect=lambda mid, **kw: saved_meals[-1])
    uow.hydration_entries = MagicMock()
    uow.hydration_entries.add = AsyncMock(side_effect=lambda entry: entry)
    uow.commit = AsyncMock()
    uow._saved_meals = saved_meals
    return uow


@pytest.mark.asyncio
async def test_scan_by_url_falls_back_to_original_url_when_compressed_url_fails():
    downloaded_urls = []

    async def fake_download(url: str) -> bytes:
        downloaded_urls.append(url)
        if "w=" in url:
            raise httpx.HTTPStatusError(
                "404 Not Found",
                request=httpx.Request("GET", url),
                response=httpx.Response(404),
            )
        return b"valid-original-image-bytes"

    uow = _make_uow()
    publisher = MagicMock()
    publisher.publish = AsyncMock()

    handler = ScanByUrlCommandHandler(
        uow=uow,
        event_bus=MagicMock(),
        vision_service=MagicMock(),
        gpt_parser=VisionResponseParser(),
        event_publisher=publisher,
        download_image_bytes=fake_download,
        cloudflare_flexible_variants_enabled=True,
    )
    handler.vision_service.analyze = AsyncMock(
        return_value={
            "structured_data": {
                "is_food": True,
                "dish_name": "Salad",
                "foods": [
                    {
                        "name": "Salad",
                        "quantity_g": 200,
                        "macros": {
                            "protein_g": 5,
                            "carbs_g": 10,
                            "fat_g": 2,
                            "fiber_g": 3,
                            "sugar_g": 2,
                        },
                    }
                ],
            }
        }
    )

    result = await handler.handle(
        ScanByUrlCommand(
            user_id=_USER_ID,
            image_url=_CF_IMAGE_URL,
            public_id=_PUBLIC_ID,
        )
    )

    assert result is not None
    assert len(downloaded_urls) == 2
    assert "w=768,fit=scale-down,f=auto" in downloaded_urls[0]
    assert downloaded_urls[1] == _CF_IMAGE_URL


@pytest.mark.asyncio
async def test_scan_by_url_uses_original_variant_when_flexible_variants_disabled():
    downloaded_urls = []

    async def fake_download(url: str) -> bytes:
        downloaded_urls.append(url)
        return b"valid-image-bytes"

    uow = _make_uow()
    publisher = MagicMock()
    publisher.publish = AsyncMock()

    handler = ScanByUrlCommandHandler(
        uow=uow,
        event_bus=MagicMock(),
        vision_service=MagicMock(),
        gpt_parser=VisionResponseParser(),
        event_publisher=publisher,
        download_image_bytes=fake_download,
        cloudflare_flexible_variants_enabled=False,
    )
    handler.vision_service.analyze = AsyncMock(
        return_value={
            "structured_data": {
                "is_food": True,
                "dish_name": "Apple",
                "foods": [
                    {
                        "name": "Apple",
                        "quantity_g": 150,
                        "macros": {
                            "protein_g": 0,
                            "carbs_g": 20,
                            "fat_g": 0,
                            "fiber_g": 3,
                            "sugar_g": 15,
                        },
                    }
                ],
            }
        }
    )

    result = await handler.handle(
        ScanByUrlCommand(
            user_id=_USER_ID,
            image_url=_CF_IMAGE_URL,
            public_id=_PUBLIC_ID,
        )
    )

    assert result is not None
    assert downloaded_urls == [_CF_IMAGE_URL]


_GATEWAY_URL = (
    "https://photos.nutreeai.com/mealtrack/eec7363d-c5d3-498b-b1d2-c698277d4f08"
)


class _ScriptedClient:
    is_closed = False

    def __init__(self, results: list[httpx.Response | Exception]) -> None:
        self._results = list(results)
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, timeout: object = None) -> httpx.Response:
        self.calls += 1
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _gateway_response(status_code: int, content: bytes = b"") -> httpx.Response:
    return httpx.Response(
        status_code,
        content=content,
        request=httpx.Request("GET", _GATEWAY_URL),
    )


def _handler_without_downloader() -> ScanByUrlCommandHandler:
    return ScanByUrlCommandHandler(uow=MagicMock(), event_bus=MagicMock())


def _install_scripted_client(monkeypatch, client: _ScriptedClient) -> list[float]:
    monkeypatch.setattr(image_download, "_scan_image_client", None)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: client)
    delays: list[float] = []

    async def _record(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(image_download.asyncio, "sleep", _record)
    return delays


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [502, 503, 504])
async def test_handler_fallback_classifies_persistent_gateway_failure(
    monkeypatch, status_code
):
    """The no-injection fallback uses the shared photo downloader.

    A persistent gateway status is one retry, then 503
    ``IMAGE_DOWNLOAD_UNAVAILABLE``. A raw ``raise_for_status()`` client would
    raise ``httpx.HTTPStatusError`` on the first response.
    """
    client = _ScriptedClient(
        [_gateway_response(status_code), _gateway_response(status_code)]
    )
    delays = _install_scripted_client(monkeypatch, client)

    with pytest.raises(ExternalServiceException) as raised:
        await _handler_without_downloader()._download_image_bytes(_GATEWAY_URL)

    assert image_download._scan_image_client is client
    assert client.calls == 2
    assert delays == [0.2]
    assert raised.value.error_code == "IMAGE_DOWNLOAD_UNAVAILABLE"
    assert raised.value.details == {}
    http_exc = handle_exception(raised.value)
    assert http_exc.status_code == 503
    assert http_exc.detail["error_code"] == "IMAGE_DOWNLOAD_UNAVAILABLE"
    assert http_exc.status_code not in (401, 403)
    assert _GATEWAY_URL not in str(http_exc.detail)


@pytest.mark.asyncio
async def test_handler_fallback_retries_one_gateway_504(monkeypatch):
    client = _ScriptedClient(
        [_gateway_response(504), _gateway_response(200, content=b"image-bytes")]
    )
    _install_scripted_client(monkeypatch, client)

    body = await _handler_without_downloader()._download_image_bytes(_GATEWAY_URL)

    assert image_download._scan_image_client is client
    assert body == b"image-bytes"
    assert client.calls == 2


@pytest.mark.asyncio
async def test_handler_fallback_maps_404_without_retry(monkeypatch):
    client = _ScriptedClient([_gateway_response(404)])
    delays = _install_scripted_client(monkeypatch, client)

    with pytest.raises(ResourceNotFoundException) as raised:
        await _handler_without_downloader()._download_image_bytes(_GATEWAY_URL)

    assert image_download._scan_image_client is client
    assert client.calls == 1
    assert delays == []
    assert raised.value.error_code == "IMAGE_NOT_FOUND"
    http_exc = handle_exception(raised.value)
    assert http_exc.status_code == 404
    assert http_exc.status_code not in (401, 403)


@pytest.mark.asyncio
async def test_handler_delegates_injected_downloader_once():
    request = httpx.Request("GET", _GATEWAY_URL)
    download = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "gateway",
            request=request,
            response=httpx.Response(504, request=request),
        )
    )
    handler = ScanByUrlCommandHandler(
        uow=MagicMock(),
        event_bus=MagicMock(),
        download_image_bytes=download,
    )

    with pytest.raises(httpx.HTTPStatusError):
        await handler._download_image_bytes(_GATEWAY_URL)

    assert download.await_count == 1
