"""HTTP client for the Open Wearables API.

The API key travels in X-Open-Wearables-API-Key. Bearer auth with that key
returns 401. Nutree reads steps and active calories only.
"""

from __future__ import annotations

from typing import Any

import httpx


class OpenWearablesError(Exception):
    """Open Wearables rejected or failed a request."""


def api_root(base_url: str) -> str:
    root = base_url.rstrip("/")
    suffix = "/api/v1"
    if root.endswith(suffix):
        root = root[: -len(suffix)]
    return root


def choose_activity_row(rows: list[dict[str, Any]], provider: str) -> dict[str, Any] | None:
    """Pick one daily row for the primary provider.

    Summary rows are per source. Adding phone and watch totals double-counts
    the same steps, so this returns a single row.
    """
    matching = [
        row
        for row in rows
        if (row.get("source") or {}).get("provider") == provider
    ]
    if not matching:
        return None
    return max(matching, key=lambda row: int(row.get("steps") or 0))


class OpenWearablesClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._root = api_root(base_url)
        self._headers = {"X-Open-Wearables-API-Key": api_key}
        self._transport = transport

    async def create_user(self) -> str:
        payload = await self._request("POST", "/api/v1/users", json={})
        user_id = payload.get("id")
        if not isinstance(user_id, str) or not user_id:
            raise OpenWearablesError("Open Wearables did not return a user id")
        return user_id

    async def authorization_url(
        self,
        provider: str,
        user_id: str,
        redirect_uri: str,
    ) -> str:
        payload = await self._request(
            "GET",
            f"/api/v1/oauth/{provider}/authorize",
            params={"user_id": user_id, "redirect_uri": redirect_uri},
        )
        url = payload.get("authorization_url")
        if not isinstance(url, str) or not url:
            raise OpenWearablesError("Open Wearables did not return an authorization URL")
        return url

    async def connections(self, user_id: str) -> list[dict[str, str]]:
        payload = await self._request(
            "GET",
            f"/api/v1/users/{user_id}/connections",
        )
        if not isinstance(payload, list):
            return []
        rows: list[dict[str, str]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            provider = item.get("provider")
            status = item.get("status")
            if isinstance(provider, str) and isinstance(status, str):
                rows.append({"provider": provider, "status": status})
        return rows

    async def activity_day(
        self,
        user_id: str,
        day: str,
        provider: str,
    ) -> tuple[int, float]:
        payload = await self._request(
            "GET",
            f"/api/v1/users/{user_id}/summaries/activity",
            params={"start_date": day, "end_date": day, "limit": 50},
        )
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return 0, 0.0
        chosen = choose_activity_row(rows, provider)
        if chosen is None:
            return 0, 0.0
        steps = int(chosen.get("steps") or 0)
        active = float(chosen.get("active_calories_kcal") or 0)
        return steps, active

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=20.0,
            ) as client:
                response = await client.request(
                    method,
                    f"{self._root}{path}",
                    headers=self._headers,
                    json=json,
                    params=params,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OpenWearablesError("Open Wearables request failed") from exc
        try:
            return response.json()
        except ValueError as exc:
            raise OpenWearablesError("Open Wearables returned invalid JSON") from exc
