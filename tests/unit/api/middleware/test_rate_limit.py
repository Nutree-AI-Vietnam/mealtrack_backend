import asyncio
import base64
import json
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.api.middleware import rate_limit as rate_limit_mod
from src.api.middleware.rate_limit import (
    create_limiter,
    get_client_ip,
    get_ip_only_rate_limit_key,
    get_user_id_or_ip,
    rate_limit_exceeded_handler,
    reset_limiter_counters,
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


def test_limiter_module_does_not_read_settings():
    assert not hasattr(rate_limit_mod, "settings")


def test_application_injects_the_redis_url():
    main = Path("src/api/main.py").read_text(encoding="utf-8")
    assert "limiter.configure(settings.redis_url)" in main


def test_limiter_uses_redis_storage_when_a_redis_url_is_configured():
    limited = create_limiter("redis://127.0.0.1:6379/0")

    assert limited.backend == "redis"
    assert limited.scheme == "async+redis"
    assert limited.key_func is get_user_id_or_ip
    assert limited.socket_timeout == 1.0


def test_tls_redis_url_uses_redis_storage():
    limited = create_limiter("rediss://127.0.0.1:6379/0")

    assert limited.backend == "redis"
    assert limited.scheme == "async+rediss"


def test_async_redis_url_is_accepted_as_is():
    limited = create_limiter("async+redis://127.0.0.1:6379/0")

    assert limited.backend == "redis"
    assert limited.scheme == "async+redis"


def test_redis_url_is_injected_explicitly():
    limited = create_limiter(None)
    assert limited.backend == "memory"
    assert limited.scheme == "async+memory"

    limited.configure("redis://127.0.0.1:6379/0")
    assert limited.backend == "redis"
    assert limited.scheme == "async+redis"

    limited.configure(None)
    assert limited.backend == "memory"
    assert limited.scheme == "async+memory"


def test_blank_redis_url_stays_on_memory_storage():
    limited = create_limiter("  ")

    assert limited.backend == "memory"
    assert limited.scheme == "async+memory"
    assert limited.key_func is get_user_id_or_ip


def test_unsupported_scheme_stays_on_memory_storage():
    limited = create_limiter("memory://")

    assert limited.backend == "memory"
    assert limited.scheme == "async+memory"


def _limited_app(limited, rule: str):
    app = FastAPI()

    @app.get("/limited")
    @limited.limit(rule)
    async def limited_route(request: Request):
        return {"ok": True}

    app.state.limiter = limited
    return app


def test_storage_construction_error_falls_open_to_memory(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(rate_limit_mod, "RedisStorage", boom)
    limited = create_limiter("redis://127.0.0.1:6379/0")
    assert limited.backend == "redis"
    client = TestClient(_limited_app(limited, "1/minute"))

    first = client.get("/limited")
    second = client.get("/limited")

    assert first.status_code == 200
    assert second.status_code == 429
    assert limited.backend == "memory"
    assert limited.key_func is get_user_id_or_ip


def test_unreachable_redis_falls_back_to_memory_without_raising():
    limited = create_limiter("redis://127.0.0.1:1/0", socket_timeout=0.3)
    assert limited.backend == "redis"
    client = TestClient(_limited_app(limited, "1/minute"))

    first = client.get("/limited")
    second = client.get("/limited")

    assert first.status_code == 200
    assert second.status_code == 429
    assert limited.backend == "memory"


def test_shared_scope_is_one_bucket_and_paths_are_not():
    limited = create_limiter(None)
    app = FastAPI()

    @app.get("/a")
    @limited.shared_limit("1/minute", scope="chat_messages")
    async def shared_a(request: Request):
        return {"ok": True}

    @app.get("/b")
    @limited.shared_limit("1/minute", scope="chat_messages")
    async def shared_b(request: Request):
        return {"ok": True}

    @app.get("/c")
    @limited.limit("1/minute")
    async def separate(request: Request):
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/a").status_code == 200
    assert client.get("/b").status_code == 429
    assert client.get("/c").status_code == 200
    assert client.get("/c").status_code == 429


class _SilentPeer:
    """TCP listener that accepts and never answers. A closed port is not this."""

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(32)
        self._sock.settimeout(0.2)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._held: list[socket.socket] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> "_SilentPeer":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        for conn in self._held:
            try:
                conn.close()
            except OSError:
                pass
        self._thread.join(timeout=1)

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            self._held.append(conn)


@pytest.mark.asyncio
async def test_blackhole_redis_fails_open_without_blocking_the_loop():
    # A silent accept is not ECONNREFUSED. The first check must wait out the
    # socket timeout, stay under this bound, and keep the loop scheduling.
    socket_timeout = 0.5
    request_bound = 1.5
    fast_bound = 0.2
    with _SilentPeer() as peer:
        started = time.perf_counter()
        limited = create_limiter(
            f"redis://127.0.0.1:{peer.port}/0",
            socket_timeout=socket_timeout,
        )
        assert time.perf_counter() - started < fast_bound
        assert limited.backend == "redis"
        assert limited.scheme == "async+redis"
        app = _limited_app(limited, "2/minute")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            ticks: list[float] = []

            async def heartbeat() -> None:
                while True:
                    ticks.append(time.perf_counter())
                    await asyncio.sleep(0.05)

            heart = asyncio.create_task(heartbeat())
            try:
                started = time.perf_counter()
                first = await asyncio.wait_for(
                    client.get("/limited"), timeout=request_bound
                )
                elapsed = time.perf_counter() - started
            finally:
                heart.cancel()
                try:
                    await heart
                except asyncio.CancelledError:
                    pass
            gaps = [ticks[index + 1] - ticks[index] for index in range(len(ticks) - 1)]

            assert first.status_code == 200
            assert elapsed < request_bound, elapsed
            assert elapsed > socket_timeout / 2, elapsed
            assert gaps, ticks
            assert max(gaps) < 0.25, gaps
            assert limited.backend == "memory"

            follow_ups = []
            for _ in range(2):
                follow_started = time.perf_counter()
                response = await asyncio.wait_for(
                    client.get("/limited"), timeout=request_bound
                )
                follow_ups.append(
                    (response.status_code, time.perf_counter() - follow_started)
                )

            assert [status for status, _latency in follow_ups] == [200, 429]
            assert all(latency < fast_bound for _status, latency in follow_ups), (
                follow_ups
            )

            # Sync fixtures call reset with no running loop. That path must
            # not open another Redis probe after the limiter has failed open.
            reset_started = time.perf_counter()
            await asyncio.to_thread(reset_limiter_counters, limited)
            reset_elapsed = time.perf_counter() - reset_started
            assert reset_elapsed < fast_bound, reset_elapsed

            again_started = time.perf_counter()
            again = await asyncio.wait_for(
                client.get("/limited"), timeout=request_bound
            )
            again_elapsed = time.perf_counter() - again_started

    assert again.status_code == 200
    assert again_elapsed < fast_bound, again_elapsed
    assert limited.backend == "memory"


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
