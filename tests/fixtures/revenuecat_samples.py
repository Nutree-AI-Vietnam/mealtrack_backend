"""RevenueCat v1 GET /subscribers response samples for adapter-level tests.

``revenuecat_subscriber_v1_200.json`` is the documented 200 sample of
"Get or Create Customer" (https://www.revenuecat.com/docs/api-v1/customers),
copied verbatim: ``request_date`` and ``request_date_ms`` sit at the response
root next to ``subscriber``. Keep it verbatim; derive variants in code.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

_SAMPLE_PATH = Path(__file__).with_name("revenuecat_subscriber_v1_200.json")

# request_date_ms of the verbatim sample.
SAMPLE_REQUEST_DATE = datetime(2019, 7, 26, 17, 40, 10, 884000, tzinfo=UTC)


def verbatim_subscriber_response() -> dict[str, Any]:
    """Return a fresh copy of the documented 200 body."""
    return json.loads(_SAMPLE_PATH.read_text(encoding="utf-8"))


def empty_customer_response(
    *,
    request_date: datetime | None = None,
    app_user_id: str = "firebase-uid",
) -> dict[str, Any]:
    """Body of a customer RevenueCat just created ("get or create").

    ``request_date`` (RevenueCat's clock at the read) defaults to now, as for
    a live read. Never a fixed date: the sync skips a snapshot older than the
    facts a row already holds, and tests stamp rows relative to now.
    """
    read_at = request_date or datetime.now(UTC)
    iso = _iso(read_at)
    return {
        "request_date": iso,
        "request_date_ms": int(read_at.timestamp() * 1000),
        "subscriber": {
            "entitlements": {},
            "first_seen": iso,
            "management_url": None,
            "non_subscriptions": {},
            "original_app_user_id": app_user_id,
            "original_application_version": None,
            "original_purchase_date": None,
            "other_purchases": {},
            "subscriptions": {},
        },
    }


def standard_annual_response(
    *,
    request_date: datetime,
    expires_date: datetime,
    store: str = "app_store",
    store_transaction_id: Any = 1000000652379790,
    billing_issues_detected_at: datetime | None = None,
    unsubscribe_detected_at: datetime | None = None,
    refunded_at: datetime | None = None,
) -> dict[str, Any]:
    """The verbatim sample with its subscription re-keyed under ``standard``.

    The app gates on the ``standard`` entitlement; everything else keeps the
    shape and value types of the documented sample (lowercase stores, numeric
    App Store ``store_transaction_id``).
    """
    body = verbatim_subscriber_response()
    body["request_date"] = request_date.strftime("%Y-%m-%dT%H:%M:%SZ")
    body["request_date_ms"] = int(request_date.timestamp() * 1000)
    subscriber = body["subscriber"]
    annual = copy.deepcopy(subscriber["subscriptions"]["annual"])
    annual.update(
        {
            "billing_issues_detected_at": _iso(billing_issues_detected_at),
            "expires_date": _iso(expires_date),
            "is_sandbox": False,
            "purchase_date": _iso(request_date.replace(year=request_date.year - 1)),
            "refunded_at": _iso(refunded_at),
            "store": store,
            "store_transaction_id": store_transaction_id,
            "unsubscribe_detected_at": _iso(unsubscribe_detected_at),
        }
    )
    subscriber["subscriptions"] = {"annual": annual}
    subscriber["non_subscriptions"] = {}
    subscriber["entitlements"] = {
        "standard": {
            "expires_date": _iso(expires_date),
            "grace_period_expires_date": None,
            "product_identifier": "annual",
            "purchase_date": annual["purchase_date"],
        }
    }
    return body


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def patched_revenuecat_http(
    payload: Any, status_code: int = 200, headers: dict[str, str] | None = None
) -> Iterator[AsyncMock]:
    """Patch the adapter's httpx client so every GET answers with ``payload``."""
    response = MagicMock(status_code=status_code)
    response.headers = headers or {}
    response.json.return_value = payload
    with patch("httpx.AsyncClient") as client:
        get = AsyncMock(return_value=response)
        client.return_value.__aenter__.return_value.get = get
        yield get
