import base64
import json

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.api.middleware.rate_limit import (
    create_limiter,
    get_client_ip,
    get_ip_only_rate_limit_key,
    get_user_id_or_ip,
    limiter,
    rate_limit_exceeded_handler,
)


class _Req:
    def __init__(self, auth: str | None, ip: str, forwarded: str | None = None):
        self.headers = {}
        if auth is not None:
            self.headers["Authorization"] = auth
        if forwarded is not None:
            self.headers["X-Forwarded-For"] = forwarded
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
    first = _Req(
        auth=f"Bearer {_jwt_with_payload({'sub': 'attacker-a'})}", ip="10.0.0.1"
    )
    second = _Req(
        auth=f"Bearer {_jwt_with_payload({'sub': 'attacker-b'})}", ip="10.0.0.1"
    )
    assert get_ip_only_rate_limit_key(first) == get_ip_only_rate_limit_key(second)
    assert get_ip_only_rate_limit_key(first) == "10.0.0.1"


@pytest.mark.parametrize(
    ("forwarded", "expected"),
    [
        ("93.184.216.9", "93.184.216.9"),
        # Render keeps a client-sent header and appends what it saw.
        ("1.1.1.1, 93.184.216.9", "93.184.216.9"),
        ("1.1.1.1, 93.184.216.9, 10.20.30.40", "93.184.216.9"),
        ("2402:800:629d::1, 10.0.0.2", "2402:800:629d::1"),
        ("not-an-ip, 93.184.216.9", "93.184.216.9"),
        ("10.0.0.2, 192.168.1.1", "10.9.9.9"),
        ("", "10.9.9.9"),
    ],
)
def test_client_ip_reads_the_rightmost_public_forwarded_address(forwarded, expected):
    assert get_client_ip(_Req(None, "10.9.9.9", forwarded)) == expected


def test_client_ip_ignores_a_spoofed_leftmost_address():
    real = _Req(None, "10.9.9.9", "93.184.216.9")
    spoofed = _Req(None, "10.9.9.9", "45.33.32.77, 93.184.216.9")
    assert get_client_ip(spoofed) == get_client_ip(real)


def test_ip_keys_use_forwarded_client_not_the_proxy_peer():
    first = _Req(None, "10.0.0.1", "93.184.216.1")
    second = _Req(None, "10.0.0.1", "93.184.216.2")
    assert get_ip_only_rate_limit_key(first) != get_ip_only_rate_limit_key(second)
    assert get_user_id_or_ip(first) == "93.184.216.1"


def _storage_name(storage: object) -> str:
    return type(storage).__name__


def test_limiter_uses_redis_storage_when_a_redis_url_is_configured():
    limited = create_limiter("redis://127.0.0.1:6379/0")

    assert _storage_name(limited._storage) == "RedisStorage"
    assert limited._key_func is get_user_id_or_ip
    assert limited._in_memory_fallback_enabled is True
    assert limited._swallow_errors is True
    assert limited._storage_options["socket_connect_timeout"] == 1.0
    assert limited._storage_options["socket_timeout"] == 1.0


def test_tls_redis_url_uses_redis_storage():
    limited = create_limiter("rediss://127.0.0.1:6379/0")

    assert _storage_name(limited._storage) == "RedisStorage"
    assert limited._in_memory_fallback_enabled is True


def test_shared_limiter_uses_the_app_redis_url():
    assert _storage_name(limiter._storage) == "RedisStorage"
    assert limiter._key_func is get_user_id_or_ip
    assert limiter._in_memory_fallback_enabled is True
    assert limiter._swallow_errors is True


def test_storage_construction_error_falls_open_to_memory(monkeypatch):
    from slowapi import Limiter

    from src.api.middleware import rate_limit as rate_limit_mod

    def fail_redis(*args, **kwargs):
        if kwargs.get("storage_uri"):
            raise RuntimeError("storage unavailable")
        return Limiter(*args, **kwargs)

    monkeypatch.setattr(rate_limit_mod, "Limiter", fail_redis)
    limited = rate_limit_mod.create_limiter("redis://127.0.0.1:6379/0")

    assert _storage_name(limited._storage) == "MemoryStorage"
    assert limited._key_func is get_user_id_or_ip
    assert limited._swallow_errors is True


def test_blank_redis_url_stays_on_memory_storage():
    limited = create_limiter("  ")

    assert _storage_name(limited._storage) == "MemoryStorage"
    assert limited._key_func is get_user_id_or_ip


def test_unreachable_redis_falls_back_to_memory_without_raising():
    limited = create_limiter("redis://127.0.0.1:1/0")
    assert _storage_name(limited._storage) == "RedisStorage"
    app = FastAPI()

    @app.get("/limited")
    @limited.limit("1/minute")
    def limited_route(request: Request):
        return {"ok": True}

    app.state.limiter = limited
    client = TestClient(app)

    first = client.get("/limited")
    second = client.get("/limited")

    assert first.status_code == 200
    assert second.status_code == 429
    assert limited._storage_dead is True
    assert _storage_name(limited._fallback_storage) == "MemoryStorage"
    assert _storage_name(limited.limiter.storage) == "MemoryStorage"


@pytest.mark.asyncio
async def test_registered_preview_rate_limit_envelope_has_retry_after():
    response = await rate_limit_exceeded_handler(
        _Req(None, "1.2.3.4"), type("Limit", (), {"retry_after": 23})()
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "23"
    assert (
        response.body
        == b'{"error":"rate_limit_exceeded","code":"preview_rate_limited"}'
    )
