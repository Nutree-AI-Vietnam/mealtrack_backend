"""Regression tests for scan-by-url photo downloads.

Sentry PYTHON-JW: `_download_image_bytes_pooled` issued one GET with
``timeout=30.0`` on the shared client (``keepalive_expiry=30``). A silent
read became ``httpx.ReadTimeout``. A photo-host 4xx was ``HTTPStatusError``,
which the event bus logs at ERROR and ``handle_exception`` turns into 500.

These tests call ``download_scan_image_bytes`` directly. When that function
is absent (the delivery tree), they exercise the old one-shot GET so the
same assertions fail there.
"""

from __future__ import annotations

import importlib
import logging

import httpx
import pytest

from src.api.exceptions import (
    ExternalServiceException,
    ImageDownloadRejected,
    MealTrackException,
    ResourceNotFoundException,
    handle_exception,
)

_PHOTO_URL = "https://photos.example.test/mealtrack/photo-1"


class _ScriptedClient:
    def __init__(self, results: list[httpx.Response | Exception]) -> None:
        self._results = list(results)
        self.calls: list[str] = []
        self.timeouts: list[object] = []

    async def get(self, url: str, timeout: object = None) -> httpx.Response:
        self.calls.append(url)
        self.timeouts.append(timeout)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _response(status_code: int, content: bytes = b"") -> httpx.Response:
    return httpx.Response(
        status_code,
        content=content,
        request=httpx.Request("GET", _PHOTO_URL),
    )


async def _delivery_download(image_url: str, *, client: _ScriptedClient) -> bytes:
    """delivery behavior: one shared-client GET, ``timeout=30.0``, then raise."""
    response = await client.get(image_url, timeout=30.0)
    response.raise_for_status()
    return response.content


def _downloader():
    try:
        image_download = importlib.import_module("src.infra.http.image_download")
    except ModuleNotFoundError:
        return _delivery_download
    return image_download.download_scan_image_bytes


def _record_backoff(monkeypatch) -> list[float]:
    delays: list[float] = []
    try:
        image_download = importlib.import_module("src.infra.http.image_download")
    except ModuleNotFoundError:
        return delays

    async def _record(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(image_download.asyncio, "sleep", _record)
    return delays


@pytest.mark.asyncio
async def test_scan_image_download_succeeds_after_one_read_timeout(monkeypatch):
    download = _downloader()
    client = _ScriptedClient(
        [
            httpx.ReadTimeout("timed out"),
            _response(200, content=b"jpeg-bytes"),
        ]
    )
    delays = _record_backoff(monkeypatch)

    body = await download(_PHOTO_URL, client=client)

    assert body == b"jpeg-bytes"
    assert client.calls == [_PHOTO_URL, _PHOTO_URL]
    assert delays == [0.2]
    timeout = client.timeouts[0]
    assert timeout.connect == 5.0
    assert timeout.read == 15.0
    assert timeout.write == 10.0
    assert timeout.pool == 5.0


@pytest.mark.asyncio
async def test_scan_image_download_two_timeouts_become_503(monkeypatch, caplog):
    download = _downloader()
    client = _ScriptedClient(
        [
            httpx.ReadTimeout("timed out"),
            httpx.ConnectError("connection reset"),
        ]
    )
    delays = _record_backoff(monkeypatch)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(ExternalServiceException) as raised:
            await download(_PHOTO_URL, client=client)

    assert len(client.calls) == 2
    assert delays == [0.2]
    assert isinstance(raised.value, MealTrackException)
    assert raised.value.error_code == "IMAGE_DOWNLOAD_UNAVAILABLE"
    http_exc = handle_exception(raised.value)
    assert http_exc.status_code == 503
    assert http_exc.detail["error_code"] == "IMAGE_DOWNLOAD_UNAVAILABLE"
    assert http_exc.detail["details"] == {}
    assert _PHOTO_URL not in str(http_exc.detail)
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_scan_image_download_retries_upstream_5xx_once(monkeypatch):
    download = _downloader()
    client = _ScriptedClient(
        [
            _response(502),
            _response(200, content=b"jpeg-bytes"),
        ]
    )
    delays = _record_backoff(monkeypatch)

    body = await download(_PHOTO_URL, client=client)

    assert body == b"jpeg-bytes"
    assert len(client.calls) == 2
    assert delays == [0.2]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [502, 503, 504])
async def test_scan_image_download_persistent_gateway_is_unavailable(
    monkeypatch, caplog, status_code
):
    """Persistent 502/503/504 is 503 IMAGE_DOWNLOAD_UNAVAILABLE.

    On the old one-shot GET this raises ``httpx.HTTPStatusError`` instead.
    """
    download = _downloader()
    client = _ScriptedClient([_response(status_code), _response(status_code)])
    delays = _record_backoff(monkeypatch)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(ExternalServiceException) as raised:
            await download(_PHOTO_URL, client=client)

    assert len(client.calls) == 2
    assert delays == [0.2]
    assert raised.value.error_code == "IMAGE_DOWNLOAD_UNAVAILABLE"
    assert raised.value.details == {}
    http_exc = handle_exception(raised.value)
    assert http_exc.status_code == 503
    assert http_exc.detail["error_code"] == "IMAGE_DOWNLOAD_UNAVAILABLE"
    assert http_exc.status_code not in (401, 403)
    assert _PHOTO_URL not in str(http_exc.detail)
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "exc_type", "http_status", "error_code"),
    [
        (404, ResourceNotFoundException, 404, "IMAGE_NOT_FOUND"),
        (403, ImageDownloadRejected, 422, "IMAGE_DOWNLOAD_REJECTED"),
        (401, ImageDownloadRejected, 422, "IMAGE_DOWNLOAD_REJECTED"),
        (429, ImageDownloadRejected, 422, "IMAGE_DOWNLOAD_REJECTED"),
    ],
)
async def test_scan_image_download_client_error_is_not_an_auth_failure(
    monkeypatch,
    caplog,
    status_code,
    exc_type,
    http_status,
    error_code,
):
    """A photo-host 4xx must not be an unexpected 500, 401, or 403."""
    download = _downloader()
    client = _ScriptedClient([_response(status_code)])
    delays = _record_backoff(monkeypatch)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(exc_type) as raised:
            await download(_PHOTO_URL, client=client)

    assert client.calls == [_PHOTO_URL]
    assert delays == []
    assert isinstance(raised.value, MealTrackException)
    assert raised.value.error_code == error_code
    http_exc = handle_exception(raised.value)
    assert http_exc.status_code == http_status
    assert http_exc.status_code not in (401, 403)
    assert http_exc.detail["details"] == {}
    assert _PHOTO_URL not in str(http_exc.detail)
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_photo_client_expires_idle_connections_and_bounds_read():
    from src.infra.http.client import close_shared_http_client, get_shared_http_client

    close_photo = None
    try:
        image_download = importlib.import_module("src.infra.http.image_download")
        photo = image_download.get_scan_image_http_client()
        close_photo = image_download.close_scan_image_http_client
    except ModuleNotFoundError:
        photo = get_shared_http_client()

    shared = get_shared_http_client()
    try:
        expiry = photo._transport._pool._keepalive_expiry
        assert photo is not shared and expiry == 5.0 and photo.timeout.read == 15.0, (
            f"dedicated={photo is not shared} keepalive_expiry={expiry} "
            f"read={photo.timeout.read}"
        )
    finally:
        if close_photo is not None:
            await close_photo()
        await close_shared_http_client()


@pytest.mark.asyncio
async def test_scan_image_download_stops_at_overall_deadline(monkeypatch):
    import asyncio

    try:
        image_download = importlib.import_module("src.infra.http.image_download")
    except ModuleNotFoundError:
        pytest.fail("delivery download has no overall deadline")

    monkeypatch.setattr(image_download, "SCAN_IMAGE_DOWNLOAD_DEADLINE_SECONDS", 0.05)

    class _Hang:
        async def get(self, url: str, timeout: object = None) -> httpx.Response:
            await asyncio.sleep(1)
            return _response(200, content=b"jpeg-bytes")

    with pytest.raises(ExternalServiceException) as raised:
        await image_download.download_scan_image_bytes(_PHOTO_URL, client=_Hang())

    assert raised.value.error_code == "IMAGE_DOWNLOAD_UNAVAILABLE"
    assert handle_exception(raised.value).status_code == 503
