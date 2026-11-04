"""USDA FoodData Central HTTP client using async httpx."""

import logging
import os
from typing import Any

import httpx

from src.domain.exceptions.food_exceptions import FoodNotFoundError
from src.domain.ports.food_data_service_port import FoodDataServicePort
from src.infra.http import get_shared_http_client
from src.infra.monitoring.secret_redaction import redact_sensitive_text

logger = logging.getLogger(__name__)


class FoodDataService(FoodDataServicePort):
    BASE_URL = "https://api.nal.usda.gov/fdc/v1"

    def __init__(
        self,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.api_key = (
            api_key if api_key is not None else os.getenv("USDA_FDC_API_KEY", "")
        )
        self._client = client

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        params = {**params, "api_key": self.api_key}
        client = self._client or get_shared_http_client()
        resp = await client.get(f"{self.BASE_URL}{path}", params=params, timeout=10)
        # ``raise_for_status`` copies the full URL, including ``api_key``, into
        # the exception message. Unknown foods are a client 404; other errors
        # keep the status without that query string.
        if resp.status_code == 404 and path.startswith("/food/"):
            _detach_query(resp)
            raise FoodNotFoundError(_fdc_id_from_path(path))
        _raise_for_status_without_secrets(resp)
        return resp.json()

    async def search_foods(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        data = await self._get("/foods/search", {"query": query, "pageSize": limit})
        return data.get("foods", [])

    async def get_food_details(self, fdc_id: int) -> dict[str, Any]:
        return await self._get(f"/food/{fdc_id}", {})

    async def get_multiple_foods(self, fdc_ids: list[int]) -> list[dict[str, Any]]:
        import asyncio

        # Semaphore caps concurrent USDA connections regardless of batch size.
        sem = asyncio.Semaphore(5)

        async def _fetch(fid: int) -> dict[str, Any]:
            async with sem:
                return await self.get_food_details(fid)

        return list(await asyncio.gather(*[_fetch(fid) for fid in fdc_ids]))

    async def get_branded_food_by_gtin(
        self, gtin_aliases: list[str]
    ) -> dict[str, Any] | None:
        aliases = {_normalize_gtin_alias(alias) for alias in gtin_aliases if alias}
        aliases.discard("")
        if not aliases or not self.api_key:
            return None

        for alias in gtin_aliases:
            try:
                data = await self._get(
                    "/foods/search",
                    {
                        "query": alias,
                        "dataType": "Branded",
                        "pageSize": 5,
                    },
                )
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning(
                    "USDA branded GTIN lookup failed: %s", type(exc).__name__
                )
                return None

            for item in data.get("foods", []) or []:
                candidate = _normalize_gtin_alias(str(item.get("gtinUpc") or ""))
                if candidate in aliases:
                    return item
        return None


def _normalize_gtin_alias(value: str) -> str:
    return "".join(ch for ch in value.strip() if ch.isdigit()).lstrip("0")


def _fdc_id_from_path(path: str) -> int:
    segment = path.removeprefix("/food/").split("/", 1)[0]
    if segment.isdigit():
        return int(segment)
    return 0


def _detach_query(resp: httpx.Response) -> httpx.Request:
    """Replace the response request so its URL no longer carries query secrets."""
    clean = httpx.Request(resp.request.method, resp.request.url.copy_with(query=None))
    try:
        resp.request = clean
    except (AttributeError, TypeError):
        return clean
    return clean


def _raise_for_status_without_secrets(resp: httpx.Response) -> None:
    if getattr(resp, "is_success", False):
        return
    clean_request = _detach_query(resp)
    status_class = resp.status_code // 100
    error_type = {
        1: "Informational response",
        3: "Redirect response",
        4: "Client error",
        5: "Server error",
    }.get(status_class, "Invalid status code")
    message = redact_sensitive_text(
        f"{error_type} '{resp.status_code} {resp.reason_phrase}' "
        f"for url '{clean_request.url}'"
    )
    raise httpx.HTTPStatusError(message, request=clean_request, response=resp)
