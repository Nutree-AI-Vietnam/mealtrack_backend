"""Unit tests for CloudflareImageStore."""

from __future__ import annotations

import pytest
import respx

from src.infra.adapters.cloudflare_image_store import CloudflareImageStore
from src.infra.config.settings import Settings


@pytest.fixture
def mock_cf_settings():
    return Settings(
        IMAGE_STORE_PROVIDER="cloudflare",
        CLOUDFLARE_ACCOUNT_ID="test-cf-account",
        CLOUDFLARE_API_TOKEN="test-cf-token",
        CLOUDFLARE_ACCOUNT_HASH="test-cf-hash",
        CLOUDFLARE_DEFAULT_VARIANT="public",
        CLOUDFLARE_CUSTOM_DOMAIN="media.test.com",
    )


@pytest.fixture
def cf_store(mock_cf_settings, monkeypatch):
    monkeypatch.setattr(
        "src.infra.adapters.cloudflare_image_store.get_settings",
        lambda: mock_cf_settings,
    )
    return CloudflareImageStore(
        account_id=mock_cf_settings.CLOUDFLARE_ACCOUNT_ID,
        api_token=mock_cf_settings.CLOUDFLARE_API_TOKEN,
        account_hash=mock_cf_settings.CLOUDFLARE_ACCOUNT_HASH,
        default_variant="public",
        custom_domain="media.test.com",
    )


def test_initialization_missing_config_raises_on_operation():
    store = CloudflareImageStore(account_id="", api_token="")
    with pytest.raises(ValueError, match="Missing Cloudflare configuration"):
        store.save(b"test", "image/jpeg")


def test_reuse_global_cloudflare_credentials_with_account_hash_only(monkeypatch):
    """Verify that CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are reused directly,
    requiring only CLOUDFLARE_ACCOUNT_HASH."""
    minimal_settings = Settings(
        CLOUDFLARE_ACCOUNT_ID="global-cf-account-id",
        CLOUDFLARE_API_TOKEN="global-cf-api-token",
        CLOUDFLARE_ACCOUNT_HASH="cf-hash-xyz",
    )
    monkeypatch.setattr(
        "src.infra.adapters.cloudflare_image_store.get_settings",
        lambda: minimal_settings,
    )
    store = CloudflareImageStore()
    assert store._account_id == "global-cf-account-id"
    assert store._api_token == "global-cf-api-token"
    assert store._account_hash == "cf-hash-xyz"
    assert store._default_variant == "public"
    assert (
        store.get_url("meal-1") == "https://imagedelivery.net/cf-hash-xyz/meal-1/public"
    )


def test_get_url_with_custom_domain(cf_store):
    url = cf_store.get_url("img-123")
    assert url == "https://media.test.com/img-123/public"

    url_thumb = cf_store.get_url("img-123", variant="thumbnail")
    assert url_thumb == "https://media.test.com/img-123/thumbnail"


def test_get_url_with_custom_domain_containing_scheme_or_slashes():
    store = CloudflareImageStore(
        account_id="test-cf-account",
        api_token="test-cf-token",
        custom_domain="https://media.test.com/",
    )
    url = store.get_url("img-123")
    assert url == "https://media.test.com/img-123/public"
    assert "https://https://" not in url


def test_get_url_with_imagedelivery_hash(mock_cf_settings, monkeypatch):
    monkeypatch.setattr(
        "src.infra.adapters.cloudflare_image_store.get_settings",
        lambda: mock_cf_settings,
    )
    store = CloudflareImageStore(
        account_id="test-cf-account",
        api_token="test-cf-token",
        account_hash="myhash123",
        custom_domain="",
    )
    url = store.get_url("img-abc")
    assert url == "https://imagedelivery.net/myhash123/img-abc/public"


def test_get_url_without_account_hash_or_custom_domain_returns_none():
    store = CloudflareImageStore(
        account_id="test-cf-account",
        api_token="test-cf-token",
        account_hash="",
        custom_domain="",
    )
    assert store.get_url("img-abc") is None
    assert store.get_url("") is None


@pytest.mark.asyncio
async def test_get_url_async_without_account_hash_or_custom_domain_returns_none():
    store = CloudflareImageStore(
        account_id="test-cf-account",
        api_token="test-cf-token",
        account_hash="",
        custom_domain="",
    )
    assert await store.get_url_async("img-abc") is None


def test_get_url_fallback_in_boolean_or_expression():
    """Verify unconfigured store returns None in 'url or store.get_url(...)', matching route usage."""
    store = CloudflareImageStore(
        account_id="test-cf-account",
        api_token="test-cf-token",
        account_hash="",
        custom_domain="",
    )
    meal_image_url = None
    resolved_url = meal_image_url or store.get_url("image-123")
    assert resolved_url is None


def test_ensure_configured_without_account_hash_or_custom_domain_raises():
    store = CloudflareImageStore(
        account_id="test-cf-account",
        api_token="test-cf-token",
        account_hash="",
        custom_domain="",
    )
    with pytest.raises(
        ValueError, match="Missing Cloudflare image delivery configuration"
    ):
        store.save(b"test", "image/jpeg")


@respx.mock
def test_save_sync_success(cf_store):
    route = respx.post(
        "https://api.cloudflare.com/client/v4/accounts/test-cf-account/images/v1"
    ).respond(
        200,
        json={
            "success": True,
            "result": {
                "id": "img-save-1",
                "variants": ["https://media.test.com/img-save-1/public"],
            },
        },
    )

    url = cf_store.save(b"fake-bytes", "image/jpeg", image_id="img-save-1")
    assert url == "https://media.test.com/img-save-1/public"
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_save_async_success(cf_store):
    route = respx.post(
        "https://api.cloudflare.com/client/v4/accounts/test-cf-account/images/v1"
    ).respond(
        200,
        json={
            "success": True,
            "result": {
                "id": "img-async-1",
                "variants": ["https://media.test.com/img-async-1/public"],
            },
        },
    )

    url = await cf_store.save_async(b"fake-bytes", "image/png", image_id="img-async-1")
    assert url == "https://media.test.com/img-async-1/public"
    assert route.called


def test_save_unsupported_content_type(cf_store):
    with pytest.raises(ValueError, match="Unsupported content type"):
        cf_store.save(b"test", "text/plain")


@respx.mock
def test_save_normalizes_content_type_with_parameters_and_aliases(cf_store):
    respx.post(
        "https://api.cloudflare.com/client/v4/accounts/test-cf-account/images/v1"
    ).respond(
        200,
        json={
            "success": True,
            "result": {
                "id": "img-norm-1",
                "variants": ["https://media.test.com/img-norm-1/public"],
            },
        },
    )

    # image/jpg alias and charset parameters should normalize to image/jpeg and succeed
    url1 = cf_store.save(b"test-bytes", "image/jpg", image_id="img-norm-1")
    assert url1 == "https://media.test.com/img-norm-1/public"

    url2 = cf_store.save(
        b"test-bytes", "image/jpeg; charset=utf-8", image_id="img-norm-2"
    )
    assert url2 == "https://media.test.com/img-norm-1/public"


@pytest.mark.asyncio
@respx.mock
async def test_load_async_success(cf_store):
    respx.get("https://media.test.com/img-123/public").respond(
        200, content=b"image-content"
    )
    data = await cf_store.load_async("img-123")
    assert data == b"image-content"


@pytest.mark.asyncio
@respx.mock
async def test_load_async_not_found(cf_store):
    respx.get("https://media.test.com/img-123/public").respond(404)
    data = await cf_store.load_async("img-123")
    assert data is None


@pytest.mark.asyncio
@respx.mock
async def test_delete_async_success(cf_store):
    respx.delete(
        "https://api.cloudflare.com/client/v4/accounts/test-cf-account/images/v1/img-123"
    ).respond(200, json={"success": True})

    result = await cf_store.delete_async("img-123")
    assert result is True


@pytest.mark.asyncio
@respx.mock
async def test_generate_upload_signature_async(cf_store):
    respx.post(
        "https://api.cloudflare.com/client/v4/accounts/test-cf-account/images/v2/direct_upload"
    ).respond(
        200,
        json={
            "success": True,
            "result": {
                "id": "new-upload-uuid",
                "uploadURL": "https://upload.imagedelivery.net/upload-key-123",
            },
        },
    )

    token = await cf_store.generate_upload_signature_async("new-upload-uuid")
    assert token["image_id"] == "new-upload-uuid"
    assert token["upload_url"] == "https://upload.imagedelivery.net/upload-key-123"
    assert token["provider"] == "cloudflare"
    assert "public_id" in token
    assert "cloud_name" in token


@respx.mock
def test_save_sync_picks_default_variant_regardless_of_order(cf_store):
    respx.post(
        "https://api.cloudflare.com/client/v4/accounts/test-cf-account/images/v1"
    ).respond(
        200,
        json={
            "success": True,
            "result": {
                "id": "img-order-1",
                "variants": [
                    "https://media.test.com/img-order-1/thumbnail",
                    "https://media.test.com/img-order-1/public",
                ],
            },
        },
    )

    url = cf_store.save(b"fake-bytes", "image/jpeg", image_id="img-order-1")
    assert url == "https://media.test.com/img-order-1/public"


@pytest.mark.asyncio
@respx.mock
async def test_save_async_picks_default_variant_regardless_of_order(cf_store):
    respx.post(
        "https://api.cloudflare.com/client/v4/accounts/test-cf-account/images/v1"
    ).respond(
        200,
        json={
            "success": True,
            "result": {
                "id": "img-order-2",
                "variants": [
                    "https://media.test.com/img-order-2/thumbnail",
                    "https://media.test.com/img-order-2/avatar",
                    "https://media.test.com/img-order-2/public",
                ],
            },
        },
    )

    url = await cf_store.save_async(b"fake-bytes", "image/jpeg", image_id="img-order-2")
    assert url == "https://media.test.com/img-order-2/public"


@respx.mock
def test_save_sync_prefers_custom_domain_over_imagedelivery_variants(cf_store):
    respx.post(
        "https://api.cloudflare.com/client/v4/accounts/test-cf-account/images/v1"
    ).respond(
        200,
        json={
            "success": True,
            "result": {
                "id": "img-fallback-1",
                "variants": [
                    "https://imagedelivery.net/test-cf-hash/img-fallback-1/public",
                ],
            },
        },
    )

    url = cf_store.save(b"fake-bytes", "image/jpeg", image_id="img-fallback-1")
    assert url == "https://media.test.com/img-fallback-1/public"


@respx.mock
def test_save_sync_without_custom_domain_picks_matching_default_variant():
    store = CloudflareImageStore(
        account_id="test-cf-account",
        api_token="test-cf-token",
        account_hash="myhash",
        custom_domain="",
        default_variant="public",
    )
    respx.post(
        "https://api.cloudflare.com/client/v4/accounts/test-cf-account/images/v1"
    ).respond(
        200,
        json={
            "success": True,
            "result": {
                "id": "img-nodefault-1",
                "variants": [
                    "https://imagedelivery.net/myhash/img-nodefault-1/thumbnail",
                    "https://imagedelivery.net/myhash/img-nodefault-1/public",
                ],
            },
        },
    )

    url = store.save(b"fake-bytes", "image/jpeg", image_id="img-nodefault-1")
    assert url == "https://imagedelivery.net/myhash/img-nodefault-1/public"


@respx.mock
def test_save_sync_raises_when_delivery_url_empty():
    store = CloudflareImageStore(
        account_id="test-cf-account",
        api_token="test-cf-token",
        account_hash="myhash",
    )
    respx.post(
        "https://api.cloudflare.com/client/v4/accounts/test-cf-account/images/v1"
    ).respond(
        200,
        json={
            "success": True,
            "result": {
                "id": "img-empty-variants",
                "variants": [],
            },
        },
    )
    # If delivery URL resolution fails to produce a URL
    store._select_delivery_url = lambda variants, image_id: ""

    with pytest.raises(RuntimeError, match="failed to resolve a delivery URL"):
        store.save(b"fake-bytes", "image/jpeg", image_id="img-empty-variants")


@pytest.mark.asyncio
@respx.mock
async def test_save_async_raises_when_delivery_url_empty():
    store = CloudflareImageStore(
        account_id="test-cf-account",
        api_token="test-cf-token",
        account_hash="myhash",
    )
    respx.post(
        "https://api.cloudflare.com/client/v4/accounts/test-cf-account/images/v1"
    ).respond(
        200,
        json={
            "success": True,
            "result": {
                "id": "img-empty-variants",
                "variants": [],
            },
        },
    )
    store._select_delivery_url = lambda variants, image_id: ""

    with pytest.raises(RuntimeError, match="failed to resolve a delivery URL"):
        await store.save_async(
            b"fake-bytes", "image/jpeg", image_id="img-empty-variants"
        )


@respx.mock
def test_save_sync_empty_variants_falls_back_to_get_url():
    store = CloudflareImageStore(
        account_id="test-cf-account",
        api_token="test-cf-token",
        account_hash="myhash",
        custom_domain="media.test.com",
        default_variant="public",
    )
    respx.post(
        "https://api.cloudflare.com/client/v4/accounts/test-cf-account/images/v1"
    ).respond(
        200,
        json={
            "success": True,
            "result": {
                "id": "img-empty-variants-ok",
                "variants": [],
            },
        },
    )
    url = store.save(b"fake-bytes", "image/jpeg", image_id="img-empty-variants-ok")
    assert url == "https://media.test.com/img-empty-variants-ok/public"


@respx.mock
def test_uuid_image_id_prefixed_for_cloudflare_compatibility(cf_store):
    raw_uuid = "cdf521da-61ff-48b7-b5c6-d0c155e243ec"
    expected_cf_id = f"mealtrack/{raw_uuid}"

    route = respx.post(
        "https://api.cloudflare.com/client/v4/accounts/test-cf-account/images/v1"
    ).respond(
        200,
        json={
            "success": True,
            "result": {
                "id": expected_cf_id,
                "variants": [
                    f"https://media.test.com/{expected_cf_id}/public",
                ],
            },
        },
    )

    url = cf_store.save(b"fake-bytes", "image/jpeg", image_id=raw_uuid)
    assert url == f"https://media.test.com/{expected_cf_id}/public"
    # Verify the request payload sent id="mealtrack/..."
    assert (
        b"mealtrack/cdf521da-61ff-48b7-b5c6-d0c155e243ec"
        in route.calls.last.request.content
    )
