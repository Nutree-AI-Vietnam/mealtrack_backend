import httpx
import pytest

from src.infra.adapters.cloudflare_images_store import (
    CloudflareImagesStore,
    select_delivery_url,
)

_PUBLIC = "https://imagedelivery.net/hash/img-1/public"
_THUMB = "https://imagedelivery.net/hash/img-1/thumbnail"


def test_init_requires_account_and_token():
    with pytest.raises(ValueError, match="CLOUDFLARE_ACCOUNT_ID"):
        CloudflareImagesStore(account_id="", api_token="token")
    with pytest.raises(ValueError, match="CLOUDFLARE_IMAGES_API_TOKEN"):
        CloudflareImagesStore(account_id="account", api_token="")


def test_from_env_prefers_images_token(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "account-1")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "ai-token")
    monkeypatch.setenv("CLOUDFLARE_IMAGES_API_TOKEN", "images-token")
    monkeypatch.setenv("CLOUDFLARE_IMAGES_VARIANT", "thumbnail")

    store = CloudflareImagesStore.from_env()

    assert store._account_id == "account-1"
    assert store._api_token == "images-token"
    assert store._variant == "thumbnail"


def test_from_env_falls_back_to_shared_token(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "account-1")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "ai-token")
    monkeypatch.delenv("CLOUDFLARE_IMAGES_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_IMAGES_VARIANT", raising=False)

    store = CloudflareImagesStore.from_env()

    assert store._api_token == "ai-token"
    assert store._variant == "public"


def test_select_delivery_url_prefers_requested_variant():
    assert select_delivery_url([_THUMB, _PUBLIC], "thumbnail") == _THUMB
    assert select_delivery_url([_THUMB, _PUBLIC], "public") == _PUBLIC


def test_select_delivery_url_falls_back_to_public_then_first():
    assert select_delivery_url([_THUMB, _PUBLIC], "missing") == _PUBLIC
    assert select_delivery_url([_THUMB], "missing") == _THUMB
    with pytest.raises(RuntimeError, match="no delivery URL"):
        select_delivery_url(["not-a-url"], "public")


@pytest.mark.asyncio
async def test_save_async_uploads_public_variant():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/accounts/account-1/images/v1")
        assert request.headers["Authorization"] == "Bearer token-1"
        body = request.content.decode("latin1")
        assert "requireSignedURLs" in body
        assert "false" in body
        assert "id" in body
        assert "catalog-1" in body
        assert "multipart/form-data" in request.headers.get("content-type", "")
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": {"id": "catalog-1", "variants": [_THUMB, _PUBLIC]},
            },
        )

    store = CloudflareImagesStore(
        account_id="account-1",
        api_token="token-1",
        transport=httpx.MockTransport(handler),
    )

    url = await store.save_async(
        b"\xff\xd8\xffbytes", "image/jpeg", image_id="catalog-1"
    )

    assert url == _PUBLIC


@pytest.mark.asyncio
async def test_save_async_uses_configured_variant():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"success": True, "result": {"variants": [_THUMB, _PUBLIC]}},
        )

    store = CloudflareImagesStore(
        account_id="account-1",
        api_token="token-1",
        variant="thumbnail",
        transport=httpx.MockTransport(handler),
    )

    assert await store.save_async(b"\x89PNG\r\n\x1a\n", "image/png") == _THUMB


@pytest.mark.asyncio
async def test_save_async_rejects_unsupported_type():
    store = CloudflareImagesStore(account_id="account-1", api_token="token-1")
    with pytest.raises(ValueError, match="Unsupported content type"):
        await store.save_async(b"bytes", "image/gif")


@pytest.mark.asyncio
async def test_save_async_maps_http_failure():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"success": False, "errors": [{"message": "denied"}]}
        )

    store = CloudflareImagesStore(
        account_id="account-1",
        api_token="token-1",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError, match="Cloudflare Images upload failed: HTTP 401"):
        await store.save_async(b"\xff\xd8\xffbytes", "image/jpeg")


def test_catalog_only_methods_are_not_implemented():
    store = CloudflareImagesStore(account_id="account-1", api_token="token-1")
    with pytest.raises(NotImplementedError, match="catalog-only"):
        store.generate_upload_signature("image-1")
