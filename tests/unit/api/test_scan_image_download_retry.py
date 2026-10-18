"""Regression tests for scan-by-url photo downloads.

Sentry PYTHON-JW: a pooled GET of a public meal photo raised httpx.ReadTimeout
inside `_download_image_bytes_pooled`. The event bus logged that as an
unexpected error (`Error handling ScanByUrlCommand`) because the timeout is
not a MealTrackException.

These tests call the download function actually registered on the scan-by-url
handler. A single transport failure must be retried once. A second failure
must surface as the existing upstream-unavailable 503, not an unexpected error.
Client 4xx responses must not be retried.
"""

from __future__ import annotations

import importlib
import logging

import httpx
import pytest

from src.api.exceptions import (
    ExternalServiceException,
    MealTrackException,
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


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    response = _response(status_code)
    return httpx.HTTPStatusError(
        f"status {status_code}",
        request=response.request,
        response=response,
    )


class _CacheStub:
    redis = object()


def _wired_download(monkeypatch):
    """Return the scan-by-url download closure installed by the composition root."""
    mod = importlib.import_module("src.api.dependencies.event_bus")
    monkeypatch.setattr(mod, "_configured_event_bus", None, raising=False)

    class _Bus:
        def __init__(self):
            self.handlers = {}

        def register_handler(self, event_type, handler):
            self.handlers[event_type] = handler

        def subscribe(self, *args, **kwargs):
            pass

    monkeypatch.setattr(mod, "PyMediatorEventBus", _Bus)

    import src.api.base_dependencies as deps

    monkeypatch.setattr(deps, "get_image_store", lambda: object())
    monkeypatch.setattr(deps, "get_vision_service", lambda: object())
    monkeypatch.setattr(deps, "get_gpt_parser", lambda: object())
    monkeypatch.setattr(deps, "get_food_cache_service", lambda: object())
    monkeypatch.setattr(deps, "get_food_data_service", lambda: object())
    monkeypatch.setattr(deps, "get_food_mapping_service", lambda: object())
    monkeypatch.setattr(deps, "get_fat_secret_service_instance", lambda: object())
    monkeypatch.setattr(deps, "get_cache_service", lambda: _CacheStub())
    monkeypatch.setattr(deps, "get_suggestion_orchestration_service", lambda: object())
    monkeypatch.setattr(deps, "get_meal_translation_service", lambda: object())
    monkeypatch.setattr(deps, "get_text_translation_service", lambda: object())

    class _Settings:
        AI_MEAL_ANALYZE_GRAPH_ENABLED = False
        AI_MEAL_ANALYZE_FATSECRET_VALIDATION_ENABLED = False
        AI_MEAL_ANALYZE_EXTERNAL_PROVIDER_TIMEOUT_SECONDS = 7.0
        AI_MEAL_ANALYZE_GRAPH_VERSION = "test"
        CLOUDFLARE_QUEUE_ACCOUNT_ID = "test-account"
        CLOUDFLARE_QUEUE_ID = "testqueue"
        CLOUDFLARE_QUEUE_API_TOKEN = "test-token"
        CLOUDFLARE_QUEUE_TIMEOUT_SECONDS = 10.0

    import src.infra.config.settings as settings_mod

    monkeypatch.setattr(settings_mod, "get_settings", lambda: _Settings())

    class _MealGen:
        pass

    import src.infra.adapters.meal_generation_service as meal_gen_mod

    monkeypatch.setattr(meal_gen_mod, "MealGenerationService", _MealGen)

    class _StubWithHandle:
        async def handle(self, *args, **kwargs):
            return None

    handler_names = [
        "EditMealCommandHandler",
        "AddCustomIngredientCommandHandler",
        "DeleteMealCommandHandler",
        "CreateManualMealCommandHandler",
        "ParseMealTextHandler",
        "SearchFoodsQueryHandler",
        "GetFoodDetailsQueryHandler",
        "GetMealByIdQueryHandler",
        "GetDailyMacrosQueryHandler",
        "GetWeeklyBudgetQueryHandler",
        "GetStreakQueryHandler",
        "GetDailyBreakdownQueryHandler",
        "GetProgressSummaryQueryHandler",
        "GetLatestWeeklyRecapQueryHandler",
        "GetNutritionBulkQueryHandler",
        "GetActivitiesPresenceQueryHandler",
        "GetDailyActivitiesQueryHandler",
        "GetBulkActivitiesQueryHandler",
        "GetMealsByDateQueryHandler",
        "DiscoverMealsCommandHandler",
        "GenerateMealRecipesCommandHandler",
        "SaveMealSuggestionCommandHandler",
        "SaveUserOnboardingCommandHandler",
        "SyncUserCommandHandler",
        "UpdateUserLastAccessedCommandHandler",
        "CompleteOnboardingCommandHandler",
        "DeleteUserCommandHandler",
        "UpdateUserMetricsCommandHandler",
        "UpdateTimezoneCommandHandler",
        "UpdateLanguageCommandHandler",
        "UpdateWeeklyAutoAdjustCommandHandler",
        "UpdateCustomMacrosCommandHandler",
        "GetUserProfileQueryHandler",
        "GetUserByFirebaseUidQueryHandler",
        "GetUserOnboardingStatusQueryHandler",
        "GetUserMetricsQueryHandler",
        "GetUserTdeeQueryHandler",
        "PreviewTdeeQueryHandler",
        "UpdateNotificationPreferencesCommandHandler",
        "GetNotificationPreferencesQueryHandler",
        "RecognizeIngredientCommandHandler",
        "MarkCheatDayCommandHandler",
        "UnmarkCheatDayCommandHandler",
        "GetCheatDaysQueryHandler",
        "AddWeightEntryCommandHandler",
        "DeleteWeightEntryCommandHandler",
        "SyncWeightEntriesCommandHandler",
        "GetWeightEntriesQueryHandler",
        "SaveSuggestionCommandHandler",
        "DeleteSavedSuggestionCommandHandler",
        "GetSavedSuggestionsQueryHandler",
        "LogMovementCommandHandler",
        "DeleteMovementEntryCommandHandler",
        "UpdateMovementEntryCommandHandler",
        "LookupBarcodeQueryHandler",
        "AttachMealPhotoCommandHandler",
        "DeleteMealPhotoCommandHandler",
    ]

    for name in handler_names:
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, lambda *a, **k: _StubWithHandle())

    bus = mod.get_configured_event_bus()
    handler = bus.handlers[mod.ScanByUrlCommand]
    return handler._download_image_bytes_fn


def _install_client(monkeypatch, client: _ScriptedClient) -> list[float]:
    import src.infra.http as http_mod

    monkeypatch.setattr(http_mod, "get_shared_http_client", lambda: client)
    delays: list[float] = []
    try:
        image_download = importlib.import_module("src.infra.http.image_download")
    except ModuleNotFoundError:
        # Pre-fix tree: the pooled GET has no retry helper, so the first
        # timeout or 5xx propagates and these tests fail before a backoff.
        return delays

    async def _record(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(image_download.asyncio, "sleep", _record)
    return delays


def _assert_one_short_backoff(delays: list[float]) -> None:
    from src.infra.http.image_download import SCAN_IMAGE_DOWNLOAD_RETRY_BACKOFF_SECONDS

    assert delays == [SCAN_IMAGE_DOWNLOAD_RETRY_BACKOFF_SECONDS]
    assert SCAN_IMAGE_DOWNLOAD_RETRY_BACKOFF_SECONDS < 1


@pytest.mark.asyncio
async def test_scan_image_download_succeeds_after_one_read_timeout(monkeypatch):
    """A stale pooled connection times out once; the fresh retry must succeed."""
    download = _wired_download(monkeypatch)
    client = _ScriptedClient(
        [
            httpx.ReadTimeout("timed out"),
            _response(200, content=b"jpeg-bytes"),
        ]
    )
    delays = _install_client(monkeypatch, client)

    body = await download(_PHOTO_URL)

    assert body == b"jpeg-bytes"
    assert client.calls == [_PHOTO_URL, _PHOTO_URL]
    _assert_one_short_backoff(delays)
    timeout = client.timeouts[0]
    assert timeout.connect == 5.0
    assert timeout.read == 30.0
    assert timeout.write == 10.0
    assert timeout.pool == 5.0


@pytest.mark.asyncio
async def test_scan_image_download_two_timeouts_become_503(monkeypatch, caplog):
    """Two transport failures become the upstream-unavailable 503, not an ERROR."""
    download = _wired_download(monkeypatch)
    client = _ScriptedClient(
        [
            httpx.ReadTimeout("timed out"),
            httpx.ConnectError("connection reset"),
        ]
    )
    delays = _install_client(monkeypatch, client)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(ExternalServiceException) as raised:
            await download(_PHOTO_URL)

    assert len(client.calls) == 2
    _assert_one_short_backoff(delays)
    assert isinstance(raised.value, MealTrackException)
    assert raised.value.error_code == "IMAGE_DOWNLOAD_UNAVAILABLE"
    http_exc = handle_exception(raised.value)
    assert http_exc.status_code == 503
    assert http_exc.detail["error_code"] == "IMAGE_DOWNLOAD_UNAVAILABLE"
    assert http_exc.detail["details"] == {}
    assert _PHOTO_URL not in str(http_exc.detail)
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_scan_image_download_does_not_retry_client_errors(monkeypatch):
    download = _wired_download(monkeypatch)
    client = _ScriptedClient([_response(404)])
    delays = _install_client(monkeypatch, client)

    with pytest.raises(httpx.HTTPStatusError) as raised:
        await download(_PHOTO_URL)

    assert raised.value.response.status_code == 404
    assert client.calls == [_PHOTO_URL]
    assert delays == []


@pytest.mark.asyncio
async def test_scan_image_download_retries_upstream_5xx_once(monkeypatch):
    download = _wired_download(monkeypatch)
    client = _ScriptedClient(
        [
            _response(502),
            _response(200, content=b"jpeg-bytes"),
        ]
    )
    delays = _install_client(monkeypatch, client)

    body = await download(_PHOTO_URL)

    assert body == b"jpeg-bytes"
    assert len(client.calls) == 2
    _assert_one_short_backoff(delays)
