"""Meal photos are stored in R2 through a locally signed upload URL."""

from __future__ import annotations

import time
import uuid

import httpx
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.base_dependencies import get_image_store
from src.api.exception_handlers import register_exception_handlers
from src.api.routes.v1.meals_direct_upload import router
from src.infra.adapters.cloudflare_image_store import CloudflareImageStore
from src.infra.adapters.meal_photo_upload import ticket_is_valid, upload_signature
from src.infra.config.settings import Settings


def _settings() -> Settings:
    return Settings(
        CLOUDFLARE_ACCOUNT_ID="acct",
        API_PUBLIC_BASE_URL="https://api.test",
        R2_ACCESS_KEY_ID="access",
        R2_SECRET_ACCESS_KEY="secret",
        R2_BUCKET="meal-photos",
        R2_PUBLIC_BASE_URL="https://photos.example.com",
        _env_file=None,
    )


def _store(monkeypatch, *, secret: str = "secret") -> CloudflareImageStore:
    monkeypatch.setattr(
        "src.infra.adapters.cloudflare_image_store.get_settings",
        _settings,
    )
    return CloudflareImageStore(
        account_id="acct",
        api_token="token",
        account_hash="hash",
        api_public_base_url="https://api.test",
        r2_access_key_id="access",
        r2_secret_access_key=secret,
        r2_bucket="meal-photos",
        r2_public_base_url="https://photos.example.com",
    )


def _client(store: CloudflareImageStore) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/v1/meals")
    app.dependency_overrides[get_image_store] = lambda: store
    return TestClient(app)


def test_upload_signature_ignores_surrounding_whitespace():
    image_id = str(uuid.uuid4())
    expires_at = int(time.time()) + 100
    signature = upload_signature("secret\n", image_id, expires_at)
    assert ticket_is_valid(" secret ", image_id, expires_at, signature)


def test_direct_upload_stores_the_photo_in_r2(monkeypatch):
    store = _store(monkeypatch, secret="secret\n")
    image_id = str(uuid.uuid4())
    ticket = store.generate_upload_signature(image_id)
    client = _client(store)

    with respx.mock:
        put = respx.put(
            url__regex=r"https://acct\.r2\.cloudflarestorage\.com/meal-photos/.*"
        ).mock(return_value=httpx.Response(200))
        response = client.post(
            ticket["upload_url"].removeprefix("https://api.test"),
            files={"file": ("meal.jpg", b"image-bytes", "image/jpeg")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["secure_url"] == f"https://photos.example.com/mealtrack/{image_id}"
    assert body["result"]["variants"] == [body["secure_url"]]
    assert put.call_count == 1
    assert image_id in body["secure_url"]


def test_direct_upload_rejects_a_bad_signature(monkeypatch):
    image_id = str(uuid.uuid4())
    client = _client(_store(monkeypatch))

    response = client.post(
        f"/v1/meals/direct-upload/{image_id}?exp=1&sig=not-a-real-signature",
        files={"file": ("meal.jpg", b"image-bytes", "image/jpeg")},
    )

    assert response.status_code == 401
    assert response.json()["detail"]["error_code"] == "INVALID_UPLOAD_TICKET"


def test_r2_public_host_is_allowed_for_scan(monkeypatch):
    import src.infra.config.settings as settings_module
    from src.api.base_dependencies import get_allowed_image_hosts

    monkeypatch.setattr(settings_module, "get_settings", _settings)
    assert "photos.example.com" in get_allowed_image_hosts()
