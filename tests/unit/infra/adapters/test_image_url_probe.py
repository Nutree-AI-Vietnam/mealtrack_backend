import httpx
import pytest

from src.infra.adapters.image_url_probe import image_url_exists


@pytest.mark.asyncio
async def test_image_url_exists_on_head_200(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "HEAD"
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)

    class _Client(httpx.AsyncClient):
        def __init__(self, **kwargs):
            kwargs["transport"] = transport
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    assert await image_url_exists(
        "https://imagedelivery.net/acct/chat-meals/tofu/public"
    )


@pytest.mark.asyncio
async def test_image_url_missing_on_head_404(monkeypatch) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(404))

    class _Client(httpx.AsyncClient):
        def __init__(self, **kwargs):
            kwargs["transport"] = transport
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    assert not await image_url_exists("https://imagedelivery.net/acct/missing/public")
