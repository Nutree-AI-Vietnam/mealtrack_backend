"""USDA FoodData Central HTTP client using async httpx."""

import logging
import os
from typing import Any

import httpx

from src.domain.ports.food_data_service_port import FoodDataServicePort
from src.infra.http import get_shared_http_client

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
        resp = await client.get(
            f"{self.BASE_URL}{path}", params=params, timeout=10
        )
        resp.raise_for_status()
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
                logger.warning("USDA branded GTIN lookup failed: %s", type(exc).__name__)
                return None

            for item in data.get("foods", []) or []:
                candidate = _normalize_gtin_alias(str(item.get("gtinUpc") or ""))
                if candidate in aliases:
                    return item
        return None


def _normalize_gtin_alias(value: str) -> str:
    return "".join(ch for ch in value.strip() if ch.isdigit()).lstrip("0")
