import base64
import json

import pytest

from src.api.middleware.rate_limit import (
    get_ip_only_rate_limit_key,
    get_user_id_or_ip,
    rate_limit_exceeded_handler,
)


class _Req:
    def __init__(self, auth: str | None, ip: str):
        self.headers = {}
        if auth is not None:
            self.headers["Authorization"] = auth
        self.client = type("C", (), {"host": ip})()


def _jwt_with_payload(payload: dict) -> str:
    header = base64.urlsafe_b64encode(b"{}").decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{header}.{body}.sig"


def test_get_user_id_or_ip_returns_uid_from_sub():
    req = _Req(auth=f"Bearer {_jwt_with_payload({'sub': 'user_1'})}", ip="1.2.3.4")
    assert get_user_id_or_ip(req) == "user_1"


def test_get_user_id_or_ip_returns_uid_from_uid_field():
    req = _Req(auth=f"Bearer {_jwt_with_payload({'uid': 'user_2'})}", ip="1.2.3.4")
    assert get_user_id_or_ip(req) == "user_2"


def test_get_user_id_or_ip_falls_back_to_ip_on_bad_token():
    req = _Req(auth="Bearer not.a.jwt", ip="5.6.7.8")
    assert get_user_id_or_ip(req) == "5.6.7.8"


def test_get_user_id_or_ip_falls_back_to_ip_when_missing_auth():
    req = _Req(auth=None, ip="9.9.9.9")
    assert get_user_id_or_ip(req) == "9.9.9.9"


def test_ip_only_key_ignores_rotating_unsigned_sub():
    first = _Req(auth=f"Bearer {_jwt_with_payload({'sub': 'attacker-a'})}", ip="10.0.0.1")
    second = _Req(auth=f"Bearer {_jwt_with_payload({'sub': 'attacker-b'})}", ip="10.0.0.1")
    assert get_ip_only_rate_limit_key(first) == get_ip_only_rate_limit_key(second)
    assert get_ip_only_rate_limit_key(first) == "10.0.0.1"


@pytest.mark.asyncio
async def test_registered_preview_rate_limit_envelope_has_retry_after():
    response = await rate_limit_exceeded_handler(
        _Req(None, "1.2.3.4"), type("Limit", (), {"retry_after": 23})()
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "23"
    assert response.body == b'{"error":"rate_limit_exceeded","code":"preview_rate_limited"}'
