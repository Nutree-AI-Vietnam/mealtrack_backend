"""
Tests for POST /v1/meals/parse-text/guest-trial — AI Handshake one-shot guest trial.

Coverage:
1. Happy path — 200 with ParseMealTextResponse shape
2. Quota already used — 409 AI_HANDSHAKE_TRIAL_USED
3. DB/quota unavailable — 503 AI_HANDSHAKE_SERVICE_UNAVAILABLE
4. Missing X-Guest-Install-Id header — 422 (FastAPI validation)
5. Empty text after sanitize — 400, quota service NOT called
6. Regression guard — existing /v1/meals/parse-text still returns 200
"""

import importlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


class _Bus:
    def __init__(self, send_impl):
        self._send_impl = send_impl

    async def send(self, msg):
        return await self._send_impl(msg)


class _Item:
    name = "egg"
    quantity = 2
    unit = "piece"
    protein = 12.0
    carbs = 1.0
    fat = 10.0
    fiber = 2.0
    sugar = 1.0
    data_source = "ai_estimate"
    fdc_id = None
    allowed_units = [
        {"unit": "piece", "gram_weight": 50.0, "description": "1 piece"},
        {"unit": "g", "gram_weight": 100.0, "description": "100 g"},
    ]


class _Resp:
    items = [_Item()]
    total_protein = 12.0
    total_carbs = 1.0
    total_fat = 10.0
    emoji = "🍳"
    unmatched_terms: list[str] = []


class _DummyImageStore:
    def get_url(self, image_id: str) -> str:
        return f"https://example.com/{image_id}"


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("GUEST_INSTALL_HASH_SECRET", "test-secret")

    sys.modules.pop("src.api.main", None)
    main = importlib.import_module("src.api.main")

    main.initialize_firebase = lambda: None  # type: ignore[assignment]

    async def _noop_async(*args, **kwargs):
        return None

    main.initialize_cache_layer = _noop_async  # type: ignore[assignment]
    main.shutdown_cache_layer = _noop_async  # type: ignore[assignment]

    from src.api.base_dependencies import get_image_store
    from src.api.dependencies.auth import (
        get_current_user_id,
        verify_firebase_token,
        verify_firebase_uid_ownership,
    )
    from src.api.dependencies.event_bus import get_configured_event_bus

    # Default noop event bus — tests that need specific behavior override per-test
    async def _default_send(msg):
        return _Resp()

    main.app.dependency_overrides[get_current_user_id] = lambda: "user_1"
    main.app.dependency_overrides[verify_firebase_token] = lambda: {"uid": "firebase_1"}
    main.app.dependency_overrides[verify_firebase_uid_ownership] = (
        lambda firebase_uid="firebase_1": firebase_uid
    )
    main.app.dependency_overrides[get_image_store] = lambda: _DummyImageStore()
    main.app.dependency_overrides[get_configured_event_bus] = lambda: _Bus(
        _default_send
    )

    yield TestClient(main.app)

    main.app.dependency_overrides = {}


def test_guest_parse_success(monkeypatch, client: TestClient):
    """POST with valid install-id and text → 200 with ParseMealTextResponse shape.

    Expected red for fiber/sugar propagation until the shared mapper is updated.
    """
    import src.api.main as main
    from src.api.dependencies.event_bus import get_configured_event_bus
    from src.api.dependencies.guest_quota import get_guest_quota_service

    async def send(msg):
        return _Resp()

    quota_svc = MagicMock()
    quota_svc.reserve = AsyncMock(return_value="abc123hash")
    quota_svc.mark_completed = AsyncMock(return_value=None)
    quota_svc.release_reservation = AsyncMock(return_value=None)

    main.app.dependency_overrides[get_configured_event_bus] = lambda: _Bus(send)
    main.app.dependency_overrides[get_guest_quota_service] = lambda: quota_svc

    r = client.post(
        "/v1/meals/parse-text/guest-trial",
        json={"text": "2 eggs scrambled", "current_items": []},
        headers={"X-Guest-Install-Id": "install-abc123"},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert "items" in body
    assert "total_calories" in body
    assert "total_protein" in body
    assert "total_carbs" in body
    assert "total_fat" in body
    assert "emoji" in body
    assert len(body["items"]) == 1
    assert body["items"][0]["name"] == "egg"
    assert body["items"][0]["allowed_units"] == [
        {"unit": "piece", "gram_weight": 50.0, "description": "1 piece"},
        {"unit": "g", "gram_weight": 100.0, "description": "100 g"},
    ]
    assert body["items"][0]["fiber"] == 2.0
    assert body["items"][0]["sugar"] == 1.0
    assert body["items"][0]["calories"] == pytest.approx(142.0)

    quota_svc.reserve.assert_awaited_once_with("install-abc123")
    quota_svc.mark_completed.assert_awaited_once_with("abc123hash")


def test_guest_parse_trial_already_used(monkeypatch, client: TestClient):
    """Quota service raises QuotaAlreadyUsedError → 409 AI_HANDSHAKE_TRIAL_USED."""
    import src.api.main as main
    from src.api.dependencies.guest_quota import get_guest_quota_service
    from src.api.services.guest_parse_quota import QuotaAlreadyUsedError

    quota_svc = MagicMock()
    quota_svc.reserve = AsyncMock(side_effect=QuotaAlreadyUsedError())

    main.app.dependency_overrides[get_guest_quota_service] = lambda: quota_svc

    r = client.post(
        "/v1/meals/parse-text/guest-trial",
        json={"text": "2 eggs scrambled", "current_items": []},
        headers={"X-Guest-Install-Id": "install-abc123"},
    )

    assert r.status_code == 409, r.text
    body = r.json()
    assert body["detail"]["error_code"] == "AI_HANDSHAKE_TRIAL_USED"


def test_guest_parse_trial_db_unavailable(monkeypatch, client: TestClient):
    """Quota service raises QuotaUnavailableError → 503 AI_HANDSHAKE_SERVICE_UNAVAILABLE."""
    import src.api.main as main
    from src.api.dependencies.guest_quota import get_guest_quota_service
    from src.api.services.guest_parse_quota import QuotaUnavailableError

    quota_svc = MagicMock()
    quota_svc.reserve = AsyncMock(side_effect=QuotaUnavailableError("DB unavailable"))

    main.app.dependency_overrides[get_guest_quota_service] = lambda: quota_svc

    r = client.post(
        "/v1/meals/parse-text/guest-trial",
        json={"text": "2 eggs scrambled", "current_items": []},
        headers={"X-Guest-Install-Id": "install-abc123"},
    )

    assert r.status_code == 503, r.text
    body = r.json()
    assert body["detail"]["error_code"] == "AI_HANDSHAKE_SERVICE_UNAVAILABLE"


def test_guest_parse_missing_install_id(monkeypatch, client: TestClient):
    """No X-Guest-Install-Id header → 422 FastAPI validation error."""
    import src.api.main as main
    from src.api.dependencies.guest_quota import get_guest_quota_service

    # Provide a dummy quota service so the dependency doesn't 503 before header validation
    quota_svc = MagicMock()
    quota_svc.reserve = AsyncMock(return_value="hash")
    main.app.dependency_overrides[get_guest_quota_service] = lambda: quota_svc

    r = client.post(
        "/v1/meals/parse-text/guest-trial",
        json={"text": "2 eggs scrambled", "current_items": []},
        # deliberately omit X-Guest-Install-Id
    )
    assert r.status_code == 422, r.text


def test_guest_parse_invalid_text(monkeypatch, client: TestClient):
    """Empty text after sanitize → 400; quota service reserve() is NOT called."""
    import src.api.main as main
    from src.api.dependencies.guest_quota import get_guest_quota_service

    quota_svc = MagicMock()
    quota_svc.reserve = AsyncMock(return_value="hash")

    main.app.dependency_overrides[get_guest_quota_service] = lambda: quota_svc

    # Text that sanitizes to empty (e.g. only whitespace/special chars)
    r = client.post(
        "/v1/meals/parse-text/guest-trial",
        json={"text": "   ", "current_items": []},
        headers={"X-Guest-Install-Id": "install-abc123"},
    )

    assert r.status_code == 400, r.text
    body = r.json()
    assert body["detail"]["error_code"] == "INVALID_MEAL_TEXT"
    # Quota reserve must NOT have been called
    quota_svc.reserve.assert_not_awaited()


def test_guest_parse_trial_requires_no_auth_dependency(monkeypatch):
    """AUTH BOUNDARY: the guest-trial route must work with NO Authorization
    header and NO auth dependency overrides at all — proving it does not sit
    behind `verify_firebase_token` / `get_current_user_id` like the
    authenticated meal routes do. Uses a fresh app import with ONLY the
    non-auth dependencies (event bus, quota service) overridden.
    """
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("GUEST_INSTALL_HASH_SECRET", "test-secret")

    import importlib
    import sys

    sys.modules.pop("src.api.main", None)
    main = importlib.import_module("src.api.main")

    main.initialize_firebase = lambda: None  # type: ignore[assignment]

    async def _noop_async(*args, **kwargs):
        return None

    main.initialize_cache_layer = _noop_async  # type: ignore[assignment]
    main.shutdown_cache_layer = _noop_async  # type: ignore[assignment]

    from src.api.dependencies.event_bus import get_configured_event_bus
    from src.api.dependencies.guest_quota import get_guest_quota_service

    async def send(msg):
        return _Resp()

    quota_svc = MagicMock()
    quota_svc.reserve = AsyncMock(return_value="abc123hash")
    quota_svc.mark_completed = AsyncMock(return_value=None)
    quota_svc.release_reservation = AsyncMock(return_value=None)

    # Deliberately NOT overriding get_current_user_id / verify_firebase_token /
    # verify_firebase_uid_ownership: if the route ever grows an auth
    # dependency, this test will fail with 401/403 instead of 200.
    main.app.dependency_overrides[get_configured_event_bus] = lambda: _Bus(send)
    main.app.dependency_overrides[get_guest_quota_service] = lambda: quota_svc

    try:
        with TestClient(main.app) as anon_client:
            r = anon_client.post(
                "/v1/meals/parse-text/guest-trial",
                json={"text": "2 eggs scrambled", "current_items": []},
                headers={"X-Guest-Install-Id": "install-abc123"},
                # deliberately no Authorization header
            )
    finally:
        main.app.dependency_overrides = {}

    assert r.status_code == 200, r.text
    quota_svc.reserve.assert_awaited_once_with("install-abc123")


def test_authenticated_parse_text_unchanged(monkeypatch, client: TestClient):
    """Regression guard: existing /v1/meals/parse-text still returns 200."""
    import src.api.main as main
    from src.api.dependencies.event_bus import get_configured_event_bus

    async def send(msg):
        return _Resp()

    main.app.dependency_overrides[get_configured_event_bus] = lambda: _Bus(send)

    r = client.post(
        "/v1/meals/parse-text",
        json={"text": "2 eggs scrambled", "current_items": []},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert "items" in body
    assert len(body["items"]) == 1
    assert body["items"][0]["allowed_units"] == [
        {"unit": "piece", "gram_weight": 50.0, "description": "1 piece"},
        {"unit": "g", "gram_weight": 100.0, "description": "100 g"},
    ]
