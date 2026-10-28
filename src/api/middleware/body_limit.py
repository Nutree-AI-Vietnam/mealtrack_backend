"""ASGI request-body limits that apply before FastAPI reads a body.

FastAPI reads the whole body, spooling multipart files to disk, before a
route's own size check can run, so these limits sit in front of it. Every
HTTP request gets one: its route's entry in BODY_LIMITS, or the default.
"""

from __future__ import annotations

import json
from re import Pattern

from starlette.routing import compile_path
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.domain.model.user.user_avatar import AVATAR_PHOTO_MAX_BYTES

KIB = 1024
MIB = 1024 * KIB

# Room for multipart boundaries and part headers on top of a file's own cap.
MULTIPART_OVERHEAD_BYTES = 64 * KIB

# Routes missing from BODY_LIMITS take small JSON bodies: a 50-item manual meal
# with serving units and nutrition snapshots is about 220 KiB.
DEFAULT_BODY_MAX_BYTES = MIB

PREVIEW_BODY_MAX_BYTES = 8 * KIB
# The meal photo routes accept files up to 10 MiB.
MEAL_PHOTO_BODY_MAX_BYTES = 10 * MIB + MULTIPART_OVERHEAD_BYTES
# Ingredient photos arrive as up to 7 MiB of base64 inside a JSON body.
INGREDIENT_PHOTO_BODY_MAX_BYTES = 8 * MIB
# Admin catalog manifests carry every recipe's ingredients and steps. A
# 720-recipe manifest, the largest the import scripts have produced, is about
# 3 to 6 MiB of JSON at typical recipe lengths.
CATALOG_MANIFEST_BODY_MAX_BYTES = 8 * MIB
# The avatar photo route accepts files up to AVATAR_PHOTO_MAX_BYTES (5 MiB).
AVATAR_PHOTO_BODY_MAX_BYTES = AVATAR_PHOTO_MAX_BYTES + MULTIPART_OVERHEAD_BYTES

# Largest body each route accepts, keyed by route path in router syntax, so
# "{image_id}" matches one path segment. Routes missing here get
# DEFAULT_BODY_MAX_BYTES, so any route whose bodies can be larger needs an
# entry; unit tests check the app's upload and manifest routes against this map.
BODY_LIMITS: dict[str, int] = {
    "/v1/tdee/preview": PREVIEW_BODY_MAX_BYTES,
    "/api/v1/tdee/preview": PREVIEW_BODY_MAX_BYTES,
    "/v1/meals/image/analyze": MEAL_PHOTO_BODY_MAX_BYTES,
    "/v1/meals/direct-upload/{image_id}": MEAL_PHOTO_BODY_MAX_BYTES,
    "/v1/ingredients/recognize": INGREDIENT_PHOTO_BODY_MAX_BYTES,
    "/v1/admin/meal-catalog/import": CATALOG_MANIFEST_BODY_MAX_BYTES,
    "/v1/admin/meal-catalog/resolve": CATALOG_MANIFEST_BODY_MAX_BYTES,
    "/v1/admin/meal-catalog/enrich": CATALOG_MANIFEST_BODY_MAX_BYTES,
    "/v1/users/avatar/photo": AVATAR_PHOTO_BODY_MAX_BYTES,
}


class _BodyTooLarge(OSError):
    """Fails the app's body read once the request passes its limit.

    An OSError, so Starlette's multipart parser closes the files it has spooled
    so far: older Starlette releases close them only on OSError or their own
    parse error, and would otherwise leave them open until garbage collection.
    """


class BodyLimitMiddleware:
    """Answer 413 to a body over its path's limit before FastAPI parses it.

    A declared Content-Length over the limit is refused before any of the body
    is read. Other bodies, chunked ones included, are counted as the app reads
    them, and the read fails as soon as the count passes the limit. Nothing is
    buffered here, so a large limit does not hold a body in memory.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.limits: list[tuple[Pattern[str], int]] = [
            (compile_path(path)[0], max_bytes)
            for path, max_bytes in BODY_LIMITS.items()
        ]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        max_bytes = self._limit_for(scope)
        if max_bytes is None:
            await self.app(scope, receive, send)
            return

        if _content_length_exceeds(scope, max_bytes):
            # A client waiting on "Expect: 100-continue" never sends this body,
            # so the connection cannot carry another request.
            await _send_too_large(send, close=_expects_continue(scope))
            return

        body_size = 0
        too_large = False
        app_response_started = False
        rejected = False

        async def limited_receive() -> Message:
            nonlocal body_size, too_large
            message = await receive()
            if message["type"] == "http.request":
                body_size += len(message.get("body", b""))
                if body_size > max_bytes:
                    too_large = True
                    raise _BodyTooLarge
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal app_response_started, rejected
            if rejected:
                return
            if message["type"] == "http.response.start":
                if too_large:
                    # FastAPI answers the failed read with a 400; send 413 instead.
                    rejected = True
                    await _send_too_large(send)
                    return
                app_response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLarge:
            if app_response_started:
                raise
        if too_large and not (rejected or app_response_started):
            await _send_too_large(send)

    def _limit_for(self, scope: Scope) -> int | None:
        if scope["type"] != "http":
            return None
        path = _route_path(scope)
        for pattern, max_bytes in self.limits:
            if pattern.match(path):
                return max_bytes
        return DEFAULT_BODY_MAX_BYTES


def _route_path(scope: Scope) -> str:
    """The path the router matches, which leaves out the app's root_path."""
    path: str = scope["path"]
    root_path: str = scope.get("root_path", "")
    if root_path and path.startswith(f"{root_path}/"):
        return path[len(root_path) :]
    return path


def _expects_continue(scope: Scope) -> bool:
    return any(
        name.lower() == b"expect" and value.lower() == b"100-continue"
        for name, value in scope.get("headers", [])
    )


def _content_length_exceeds(scope: Scope, max_bytes: int) -> bool:
    for name, value in scope.get("headers", []):
        if name.lower() != b"content-length":
            continue
        try:
            if int(value) > max_bytes:
                return True
        except ValueError:
            continue
    return False


async def _send_too_large(send: Send, *, close: bool = False) -> None:
    body = json.dumps(
        {"detail": {"code": "request_too_large"}}, separators=(",", ":")
    ).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]
    if close:
        headers.append((b"connection", b"close"))
    await send({"type": "http.response.start", "status": 413, "headers": headers})
    await send({"type": "http.response.body", "body": body})
