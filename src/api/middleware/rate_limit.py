"""Request rate limits.

Redis counters are checked with ``limits``' async redis-py client
(``async+redis://`` / ``async+rediss://``), awaited from the route decorator.
A slow Redis therefore does not block the event loop. The Redis URL is
injected through :meth:`RateLimitGuard.configure`; this module does not read
application settings.

If Redis errors, the process keeps using an in-memory fixed window for the
rest of its lifetime and does not probe Redis again on the request path.
A storage failure never fails the request: the in-memory window still applies,
and if that window itself errors the request is allowed.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import inspect
import ipaddress
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi.responses import JSONResponse
from limits import RateLimitItem, parse_many
from limits.aio.storage import RedisStorage
from limits.aio.strategies import FixedWindowRateLimiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from slowapi.wrappers import Limit
from starlette.requests import Request

logger = logging.getLogger(__name__)

# How long one Redis command may wait before the limiter fails open.
# Injected URLs reuse settings.redis_url; this is not a separate setting.
_REDIS_SOCKET_TIMEOUT_SECONDS = 1.0
_MEMORY_SWEEP_SECONDS = 60.0

# Published Cloudflare proxy ranges.
# Source: https://www.cloudflare.com/ips-v4 and https://www.cloudflare.com/ips-v6
# Vendored 2026-10-21. Do not fetch these at runtime.
_CLOUDFLARE_IP_RANGES: tuple[str, ...] = (
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
    "2400:cb00::/32",
    "2606:4700::/32",
    "2803:f800::/32",
    "2405:b500::/32",
    "2405:8100::/32",
    "2a06:98c0::/29",
    "2c0f:f248::/32",
)

_CLOUDFLARE_NETWORKS = tuple(
    ipaddress.ip_network(cidr) for cidr in _CLOUDFLARE_IP_RANGES
)


def _public_ip(value: str) -> str | None:
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    return str(ip) if ip.is_global else None


def _is_cloudflare_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return False
    return any(ip in network for network in _CLOUDFLARE_NETWORKS)


def _parsed_ip(value: str) -> str | None:
    """Canonical IP text, or None when the value is not an IP."""
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def _rightmost_public_or_peer(request) -> str:
    """#761 selection: right-most public X-Forwarded-For hop, else the socket peer.

    The socket peer is Render's internal proxy (10.x), shared by every caller.
    Render appends the address it saw to X-Forwarded-For but keeps whatever the
    client sent, so the leftmost entries can be faked. Reading from the right,
    the first public address is the one Render's edge recorded; a client can
    only add entries to its left. Without a public entry, the peer address is used.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    for entry in reversed(forwarded.split(",")):
        ip = _public_ip(entry)
        if ip:
            return ip
    return get_remote_address(request)


def get_client_ip(request) -> str:
    """Client IP for rate-limit keys and request logs.

    Starts from the #761 hop. When that hop is inside Cloudflare's published
    ranges, the visitor IP is CF-Connecting-IP. Trust that header only in that
    case, and only when it parses as an IP. Otherwise keep the #761 hop, which
    leaves direct Render traffic unchanged.
    """
    hop = _rightmost_public_or_peer(request)
    if _is_cloudflare_ip(hop):
        connecting = _parsed_ip(request.headers.get("CF-Connecting-IP") or "")
        if connecting is not None:
            return connecting
    return hop


def get_user_id_or_ip(request):
    """Extract user ID from JWT payload for per-user limits, fallback to IP.

    Uses lightweight base64 decode of JWT payload (no verification).
    Auth is already enforced by verify_firebase_token dependency —
    this only needs a stable key for rate limiting.
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            # Decode JWT payload without verification (just for rate limit key)
            payload_b64 = auth[7:].split(".")[1]
            # Add padding for base64
            padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded))
            uid = payload.get("sub") or payload.get("uid")
            if uid:
                return uid
        except Exception:
            pass
    return get_client_ip(request)


def get_ip_only_rate_limit_key(request):
    """Ignore Authorization. Unauthenticated routes must not key on JWT `sub`."""
    return get_client_ip(request)


class _MemoryFixedWindow:
    """In-process fixed window. No timer thread and no event-loop lock."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._expires_at: dict[str, float] = {}
        self._next_sweep = 0.0

    def hit(self, key: str, limit: int, expiry: int, cost: int) -> bool:
        now = time.monotonic()
        self._sweep(now)
        if self._expires_at.get(key, 0.0) <= now:
            self._counts.pop(key, None)
            self._expires_at.pop(key, None)
        current = self._counts.get(key, 0) + cost
        self._counts[key] = current
        if current == cost:
            self._expires_at[key] = now + expiry
        return current <= limit

    def _sweep(self, now: float) -> None:
        if now < self._next_sweep:
            return
        self._next_sweep = now + _MEMORY_SWEEP_SECONDS
        expired = [key for key, expiry in self._expires_at.items() if expiry <= now]
        for key in expired:
            self._counts.pop(key, None)
            self._expires_at.pop(key, None)

    def clear(self) -> None:
        self._counts.clear()
        self._expires_at.clear()
        self._next_sweep = 0.0


def _request_index(func: Callable[..., Any]) -> int:
    for index, parameter in enumerate(inspect.signature(func).parameters.values()):
        if parameter.name in {"request", "websocket"}:
            return index
    raise Exception(f'No "request" or "websocket" argument on function "{func}"')


def _request_from_call(
    args: tuple[Any, ...], kwargs: dict[str, Any], index: int
) -> Request:
    request = kwargs.get("request", args[index] if len(args) > index else None)
    if not isinstance(request, Request):
        raise Exception(
            "parameter `request` must be an instance of starlette.requests.Request"
        )
    return request


def _async_redis_target(storage_uri: str | None) -> tuple[str, str] | None:
    """Return ``(async_uri, scheme)`` or None to stay on memory.

    ``scheme`` is ``async+redis`` or ``async+rediss`` and never includes the host.
    """
    raw = (storage_uri or "").strip()
    if not raw:
        return None
    for prefix, scheme in (
        ("async+rediss://", "async+rediss"),
        ("async+redis://", "async+redis"),
        ("rediss://", "async+rediss"),
        ("redis://", "async+redis"),
    ):
        if raw.startswith(prefix):
            if prefix.startswith("async+"):
                return raw, scheme
            return f"async+{raw}", scheme
    logger.warning(
        "Rate limiter storage scheme %s is unsupported; using in-memory storage",
        raw.split(":", 1)[0],
    )
    return None


@dataclass(frozen=True)
class _RouteLimit:
    items: tuple[RateLimitItem, ...]
    key_func: Callable[..., Any]
    key_takes_request: bool
    scope: str | None
    per_method: bool
    methods: tuple[str, ...] | None
    error_message: str | None
    exempt_when: Callable[..., bool] | None
    exempt_takes_request: bool
    cost: int | Callable[..., int]
    override_defaults: bool


class RateLimitGuard:
    """Async fixed-window limiter with an injected Redis URL and memory fallback."""

    def __init__(self) -> None:
        self.key_func: Callable[..., Any] = get_user_id_or_ip
        self.socket_timeout = _REDIS_SOCKET_TIMEOUT_SECONDS
        self._memory = _MemoryFixedWindow()
        self._redis_uri: str | None = None
        self._scheme = "async+memory"
        self._redis_failed = False
        self._redis: FixedWindowRateLimiter | None = None
        self._redis_loop: asyncio.AbstractEventLoop | None = None

    @property
    def backend(self) -> str:
        """``redis`` while Redis is the active store, otherwise ``memory``."""
        if self._redis_uri is not None and not self._redis_failed:
            return "redis"
        return "memory"

    @property
    def scheme(self) -> str:
        """Configured storage scheme, without host or credentials."""
        return self._scheme

    def configure(
        self,
        storage_uri: str | None,
        *,
        socket_timeout: float = _REDIS_SOCKET_TIMEOUT_SECONDS,
    ) -> None:
        """Select Redis or memory. Does not connect; the client is loop-local."""
        self.socket_timeout = socket_timeout
        self._memory.clear()
        self._redis_failed = False
        self._redis = None
        self._redis_loop = None
        self._redis_uri = None
        self._scheme = "async+memory"
        target = _async_redis_target(storage_uri)
        if target is None:
            return
        uri, scheme = target
        self._redis_uri = uri
        self._scheme = scheme

    def limit(
        self,
        limit_value: str,
        key_func: Callable[..., Any] | None = None,
        per_method: bool = False,
        methods: list[str] | None = None,
        error_message: str | None = None,
        exempt_when: Callable[..., bool] | None = None,
        cost: int | Callable[..., int] = 1,
        override_defaults: bool = True,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._decorate(
            limit_value,
            key_func=key_func,
            scope=None,
            per_method=per_method,
            methods=methods,
            error_message=error_message,
            exempt_when=exempt_when,
            cost=cost,
            override_defaults=override_defaults,
        )

    def shared_limit(
        self,
        limit_value: str,
        scope: str,
        key_func: Callable[..., Any] | None = None,
        error_message: str | None = None,
        exempt_when: Callable[..., bool] | None = None,
        cost: int | Callable[..., int] = 1,
        override_defaults: bool = True,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._decorate(
            limit_value,
            key_func=key_func,
            scope=scope,
            per_method=False,
            methods=None,
            error_message=error_message,
            exempt_when=exempt_when,
            cost=cost,
            override_defaults=override_defaults,
        )

    def clear_memory(self) -> None:
        self._memory.clear()

    async def reset_redis(self) -> None:
        """Clear Redis keys on a fresh client. A failure sticks to memory.

        The request-path client stays on the application loop. Reset opens its
        own client so a sync test caller cannot bind that client to a loop it
        then closes. Failure does not schedule a later probe.
        """
        if self._redis_uri is None or self._redis_failed:
            return
        try:
            storage = _open_redis(self._redis_uri, self.socket_timeout)
        except Exception as exc:
            self._mark_redis_failed(exc)
            return
        try:
            await asyncio.wait_for(storage.reset(), timeout=self.socket_timeout)
        except Exception as exc:
            self._mark_redis_failed(exc)
        finally:
            await _close_redis(storage)

    def _decorate(
        self,
        limit_value: str,
        *,
        key_func: Callable[..., Any] | None,
        scope: str | None,
        per_method: bool,
        methods: list[str] | None,
        error_message: str | None,
        exempt_when: Callable[..., bool] | None,
        cost: int | Callable[..., int],
        override_defaults: bool,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        keyfunc = key_func or self.key_func
        route = _RouteLimit(
            items=tuple(parse_many(limit_value)),
            key_func=keyfunc,
            key_takes_request="request" in inspect.signature(keyfunc).parameters,
            scope=scope,
            per_method=per_method,
            methods=tuple(method.lower() for method in methods) if methods else None,
            error_message=error_message,
            exempt_when=exempt_when,
            exempt_takes_request=(
                exempt_when is not None
                and len(inspect.signature(exempt_when).parameters) == 1
            ),
            cost=cost,
            override_defaults=override_defaults,
        )

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            request_index = _request_index(func)

            if asyncio.iscoroutinefunction(func):

                @functools.wraps(func)
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    request = _request_from_call(args, kwargs, request_index)
                    await self._check(request, route)
                    return await func(*args, **kwargs)

                return async_wrapper

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                # Sync routes run in FastAPI's threadpool, off the application
                # loop. asyncio.run keeps the Redis await off that loop too.
                request = _request_from_call(args, kwargs, request_index)
                asyncio.run(self._check(request, route))
                return func(*args, **kwargs)

            return sync_wrapper

        return decorator

    async def _check(self, request: Request, route: _RouteLimit) -> None:
        path = request.scope.get("path") or ""
        bucket = route.scope or path
        if not bucket:
            return
        if route.methods is not None and request.method.lower() not in route.methods:
            return
        if route.exempt_when is not None:
            exempt = (
                route.exempt_when(request)
                if route.exempt_takes_request
                else route.exempt_when()
            )
            if exempt:
                return
        if route.per_method:
            bucket = f"{bucket}:{request.method}"
        limit_key = (
            route.key_func(request) if route.key_takes_request else route.key_func()
        )
        if not limit_key or not bucket:
            return
        cost = route.cost(request) if callable(route.cost) else route.cost
        for item in route.items:
            allowed = await self._hit(item, str(limit_key), bucket, int(cost))
            if allowed:
                continue
            logger.warning(
                "Rate limit exceeded at %s (%s/%ss)",
                bucket,
                item.amount,
                item.get_expiry(),
            )
            raise RateLimitExceeded(
                Limit(
                    item,
                    route.key_func,
                    route.scope,
                    route.per_method,
                    list(route.methods) if route.methods is not None else None,
                    route.error_message,
                    route.exempt_when,
                    route.cost,
                    route.override_defaults,
                )
            )

    async def _hit(self, item: RateLimitItem, key: str, bucket: str, cost: int) -> bool:
        strategy = await self._redis_strategy()
        if strategy is not None:
            try:
                return await strategy.hit(item, key, bucket, cost=cost)
            except Exception as exc:
                self._mark_redis_failed(exc)
        try:
            return self._memory.hit(
                item.key_for(key, bucket), item.amount, item.get_expiry(), cost
            )
        except Exception as exc:
            logger.warning(
                "Rate limiter memory storage failed (%s); allowing request",
                type(exc).__name__,
            )
            return True

    async def _redis_strategy(self) -> FixedWindowRateLimiter | None:
        if self._redis_uri is None or self._redis_failed:
            return None
        loop = asyncio.get_running_loop()
        if self._redis is not None and self._redis_loop is loop:
            return self._redis
        try:
            storage = _open_redis(self._redis_uri, self.socket_timeout)
        except Exception as exc:
            self._mark_redis_failed(exc)
            return None
        self._redis = FixedWindowRateLimiter(storage)
        self._redis_loop = loop
        return self._redis

    def _mark_redis_failed(self, exc: BaseException) -> None:
        if self._redis_failed:
            return
        self._redis_failed = True
        self._redis = None
        self._redis_loop = None
        logger.warning(
            "Rate limiter Redis unavailable (%s); using in-memory storage",
            type(exc).__name__,
        )


def _open_redis(uri: str, socket_timeout: float) -> RedisStorage:
    # options are float | str | bool. Timeouts are floats; no Retry object.
    return RedisStorage(
        uri,
        implementation="redispy",
        socket_connect_timeout=socket_timeout,
        socket_timeout=socket_timeout,
    )


async def _close_redis(storage: RedisStorage) -> None:
    bridge = getattr(storage, "bridge", None)
    client = getattr(bridge, "storage", None)
    close = getattr(client, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except Exception as exc:
        logger.debug("Rate limiter Redis client close failed (%s)", type(exc).__name__)


def create_limiter(
    storage_uri: str | None,
    *,
    socket_timeout: float = _REDIS_SOCKET_TIMEOUT_SECONDS,
) -> RateLimitGuard:
    """Build a limiter for ``storage_uri``. A blank URI stays in memory."""
    guard = RateLimitGuard()
    guard.configure(storage_uri, socket_timeout=socket_timeout)
    return guard


def reset_limiter_counters(target: RateLimitGuard | None = None) -> None:
    """Clear counters. A down Redis does not raise and is not retried later.

    Callers are synchronous test fixtures. Memory is cleared here. Redis is
    reset on a throwaway client only when this thread has no running loop, so
    the request-path client is never moved onto a loop that is about to close.
    """
    active = limiter if target is None else target
    active.clear_memory()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(active.reset_redis())


# Memory until the application injects settings.redis_url. Route decorators
# bind this instance, so configure() mutates it instead of replacing it.
limiter = RateLimitGuard()


async def rate_limit_exceeded_handler(request, exc: RateLimitExceeded):
    """Stable public 429 envelope for clients that need deterministic backoff."""
    response = JSONResponse(
        status_code=429,
        content={"error": "rate_limit_exceeded", "code": "preview_rate_limited"},
    )
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    else:
        response.headers["Retry-After"] = "60"
    return response
