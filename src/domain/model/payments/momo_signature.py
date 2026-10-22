"""HMAC-SHA256 signatures for the MoMo payment gateway (API v2).

MoMo signs the raw string `key=value&...` with the partner secret key. The
field order is fixed per message type and is alphabetical.
"""

import hashlib
import hmac
from collections.abc import Mapping

CREATE_FIELDS = (
    "accessKey",
    "amount",
    "extraData",
    "ipnUrl",
    "orderId",
    "orderInfo",
    "partnerCode",
    "redirectUrl",
    "requestId",
    "requestType",
)
QUERY_FIELDS = ("accessKey", "orderId", "partnerCode", "requestId")
# Shared by the IPN body and the redirect query string.
RESULT_FIELDS = (
    "accessKey",
    "amount",
    "extraData",
    "message",
    "orderId",
    "orderInfo",
    "orderType",
    "partnerCode",
    "payType",
    "requestId",
    "responseTime",
    "resultCode",
    "transId",
)


def momo_signature(
    secret_key: str, values: Mapping[str, object], fields: tuple[str, ...]
) -> str:
    raw = "&".join(f"{field}={_text(values.get(field))}" for field in fields)
    return hmac.new(secret_key.encode(), raw.encode(), hashlib.sha256).hexdigest()


def is_valid_result_signature(
    secret_key: str, access_key: str, payload: Mapping[str, object]
) -> bool:
    """Check a MoMo IPN body or redirect query against its signature."""
    received = payload.get("signature")
    if not isinstance(received, str) or not received:
        return False
    expected = momo_signature(
        secret_key, {**payload, "accessKey": access_key}, RESULT_FIELDS
    )
    return hmac.compare_digest(expected, received)


def _text(value: object) -> str:
    return "" if value is None else str(value)
