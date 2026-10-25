"""HTTP client for the Open Wearables API.

The API key travels in X-Open-Wearables-API-Key. Bearer auth with that key
returns 401.
"""

from __future__ import annotations

import math
from typing import Any

import httpx

from src.infra.adapters.open_wearables_sync_runs import summarize_sync_runs


class OpenWearablesError(Exception):
    """Open Wearables rejected or failed a request."""


def api_root(base_url: str) -> str:
    root = base_url.rstrip("/")
    suffix = "/api/v1"
    if root.endswith(suffix):
        root = root[: -len(suffix)]
    return root


def _workout_row(row: object) -> dict[str, object] | None:
    if not isinstance(row, dict):
        return None
    workout_id = row.get("id")
    if not isinstance(workout_id, str) or not workout_id:
        return None
    name = row.get("name") or row.get("type") or "Activity"
    duration = row.get("duration_seconds")
    calories = row.get("calories_kcal")
    minutes = duration // 60 if isinstance(duration, int) else 0
    kcal = float(calories) if isinstance(calories, (int, float)) else 0.0
    return {
        "id": workout_id,
        "name": name if isinstance(name, str) else "Activity",
        "duration_min": minutes if minutes > 0 else 1,
        "calories_kcal": kcal,
    }


def _number(raw: object) -> float:
    """A numeric sample value, or 0 when the API sends something unparseable."""
    if isinstance(raw, bool):
        return 0.0
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str):
        try:
            value = float(raw)
        except ValueError:
            return 0.0
    else:
        return 0.0
    return value if math.isfinite(value) else 0.0


def _names_missing_user(response: httpx.Response, user_id: str) -> bool:
    """True for Open Wearables' own missing-user reply, which names the id."""
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        return False
    if not isinstance(detail, str):
        return False
    text = detail.lower()
    return user_id.lower() in text and "not found" in text


def choose_activity_row(
    rows: list[dict[str, Any]], provider: str
) -> dict[str, Any] | None:
    """One daily row for the chosen provider. Summing sources double-counts steps."""
    matching = [
        row for row in rows if (row.get("source") or {}).get("provider") == provider
    ]
    if not matching:
        return None
    return max(matching, key=lambda row: _number(row.get("steps")))


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
            raise OpenWearablesError(
                "Open Wearables did not return an authorization URL"
            )
        return url

    async def connections(self, user_id: str) -> list[dict[str, str]]:
        payload = await self._request("GET", f"/api/v1/users/{user_id}/connections")
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

    async def sync_states(self, user_id: str) -> dict[str, dict[str, str | None]]:
        """Newest sync outcome per provider over the last 24 hours."""
        payload = await self._request(
            "GET",
            f"/api/v1/users/{user_id}/sync/runs",
            params={"limit": 50},
        )
        return summarize_sync_runs(payload)

    async def disconnect(self, user_id: str, provider: str) -> bool:
        """Revoke the provider sign-in and delete the data synced from it.

        Returns False when Open Wearables has no connection for this user and
        provider. Any other 404 raises, as for ``delete_user``.
        """
        response = await self._send(
            "DELETE",
            f"/api/v1/users/{user_id}/connections/{provider}/data",
            missing_ok=True,
        )
        if response.status_code != httpx.codes.NOT_FOUND:
            return True
        if _names_missing_user(response, user_id):
            return False
        raise OpenWearablesError("Open Wearables answered 404 without naming the user")

    async def activity_day(
        self, user_id: str, bounds: tuple[str, str], provider: str
    ) -> tuple[int, float]:
        # A chosen watch can lose the daily summary contest, so read its samples.
        start_time, end_time = bounds
        steps_samples: list[float] = []
        energy_samples: list[float] = []
        daily_steps: list[float] = []
        daily_energy: list[float] = []
        cursor: str | None = None
        for _ in range(20):
            params: dict[str, Any] = {
                "start_time": start_time,
                "end_time": end_time,
                "types": ["steps", "active_energy"],
                "provider": provider,
                "limit": 1000,
                "filter_by_priority": "false",
            }
            if cursor:
                params["cursor"] = cursor
            payload = await self._request(
                "GET",
                f"/api/v1/users/{user_id}/timeseries",
                params=params,
            )
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                value = _number(row.get("value"))
                daily = bool(row.get("is_daily_total"))
                if row.get("type") == "steps":
                    (daily_steps if daily else steps_samples).append(value)
                elif row.get("type") == "active_energy":
                    (daily_energy if daily else energy_samples).append(value)
            pagination = (
                payload.get("pagination") if isinstance(payload, dict) else None
            )
            cursor = (
                pagination.get("next_cursor") if isinstance(pagination, dict) else None
            )
            if (
                not isinstance(pagination, dict)
                or not pagination.get("has_more")
                or not cursor
            ):
                break
        steps = daily_steps or steps_samples
        energy = daily_energy or energy_samples
        return int(round(sum(steps))), float(sum(energy))

    async def workouts_on_day(
        self, user_id: str, bounds: tuple[str, str], provider: str
    ) -> list[dict[str, object]]:
        start_date, end_date = bounds
        payload = await self._request(
            "GET",
            f"/api/v1/users/{user_id}/events/workouts",
            params={
                "start_date": start_date,
                "end_date": end_date,
                "provider": provider,
                "limit": 20,
            },
        )
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []
        workouts: list[dict[str, object]] = []
        for row in rows:
            parsed = _workout_row(row)
            if parsed is not None:
                workouts.append(parsed)
        return workouts

    async def delete_user(self, user_id: str) -> bool:
        """Delete the user, their synced data and provider tokens.

        Returns False when Open Wearables reports that this user does not
        exist, so a retried deletion can finish. Any other 404, such as a
        wrong path behind a proxy, raises: it does not show the data is gone.
        """
        response = await self._send(
            "DELETE", f"/api/v1/users/{user_id}", missing_ok=True
        )
        if response.status_code != httpx.codes.NOT_FOUND:
            return True
        if _names_missing_user(response, user_id):
            return False
        raise OpenWearablesError("Open Wearables answered 404 without naming the user")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        response = await self._send(method, path, json=json, params=params)
        try:
            return response.json()
        except ValueError as exc:
            raise OpenWearablesError("Open Wearables returned invalid JSON") from exc

    async def _send(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        missing_ok: bool = False,
    ) -> httpx.Response:
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
                if not (missing_ok and response.status_code == httpx.codes.NOT_FOUND):
                    response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OpenWearablesError("Open Wearables request failed") from exc
        return response
