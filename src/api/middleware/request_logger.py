"""
Request/Response logging middleware.
Adds request ID tracking and timing for all API calls.
Pure ASGI implementation — does not subclass BaseHTTPMiddleware, so it never
buffers the request or response body.
"""

import asyncio
import logging
import time
import uuid

from fastapi import Request
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.observability import set_request_context
from src.planner_observability import (
    planner_operation,
    planner_request_context,
    record_planner_phase,
)
from src.planner_request_policy import planner_deadline

logger = logging.getLogger(__name__)


class RequestLoggerMiddleware:
    """
    Pure ASGI middleware for logging all HTTP requests and responses.

    Features:
    - Generates unique request ID for tracing
    - Logs request method, path, and timing
    - Adds X-Request-ID and X-Response-Time headers to responses
    - Logs slow requests (>1s) at WARNING level
    """

    SLOW_REQUEST_THRESHOLD_SECONDS = 1.0
    AI_REQUEST_TIMEOUT_SECONDS = 30.0

    SKIP_PATHS = {
        "/health",
        "/v1/health",
        "/docs",
        "/openapi.json",
        "/redoc",
    }

    def __init__(self, app: ASGIApp, log_body: bool = False) -> None:
        self.app = app
        self.log_body = log_body

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        if request.url.path in self.SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex[:8]
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id
        scope["state"]["planner_started_at"] = time.monotonic()
        set_request_context(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            user_id=self._get_user_id(request),
        )

        with planner_request_context(
            planner_operation(request.url.path, request.method), request_id
        ):
            await self._handle_request(request, scope, receive, send, request_id)

    async def _handle_request(
        self,
        request: Request,
        scope: Scope,
        receive: Receive,
        send: Send,
        request_id: str,
    ) -> None:
        start_time = time.perf_counter()
        self._log_request(request, request_id)

        response_logged = False
        request_result = "success"

        async def send_with_headers(message: Message) -> None:
            nonlocal response_logged, request_result
            if message["type"] == "http.response.start":
                request_result = "error" if message["status"] >= 400 else "success"
                elapsed = time.perf_counter() - start_time
                headers = MutableHeaders(scope=message)
                headers.append("X-Request-ID", request_id)
                headers.append("X-Response-Time", f"{elapsed:.3f}s")
                await send(message)
                # Log after delivery so elapsed reflects actual time-to-first-byte
                self._log_response(request, message["status"], request_id, elapsed)
                record_planner_phase(
                    "response_start",
                    time.perf_counter() - start_time,
                    result=request_result,
                )
                response_logged = True
            else:
                await send(message)

        try:
            if planner_operation(request.url.path, request.method) == "ai_prompt":
                deadline = (
                    scope["state"]["planner_started_at"]
                    + self.AI_REQUEST_TIMEOUT_SECONDS
                )
                with planner_deadline(deadline):
                    async with asyncio.timeout_at(deadline):
                        await self.app(scope, receive, send_with_headers)
            else:
                await self.app(scope, receive, send_with_headers)
        except TimeoutError as error:
            request_result = "error"
            if planner_operation(request.url.path, request.method) != "ai_prompt":
                elapsed = time.perf_counter() - start_time
                if not response_logged:
                    self._log_response(request, 500, request_id, elapsed)
                self._log_error(request, request_id, elapsed, error)
                raise
            if response_logged:
                # Headers already delivered cannot be replaced by a second
                # HTTP response. Let the streaming failure propagate.
                raise
            response = JSONResponse(
                status_code=503,
                content={
                    "detail": {
                        "error_code": "AI_MEAL_PLAN_UNAVAILABLE",
                        "message": "AI meal plan adjustment exceeded its deadline",
                    }
                },
            )
            await response(scope, receive, send_with_headers)
        except asyncio.CancelledError:
            request_result = "cancelled"
            raise
        except Exception as e:
            request_result = "error"
            elapsed = time.perf_counter() - start_time
            # Only log [RES-...] if http.response.start was never sent — avoids a
            # duplicate log line when the app raises mid-stream after headers went out.
            if not response_logged:
                self._log_response(request, 500, request_id, elapsed)
            self._log_error(request, request_id, elapsed, e)
            raise
        finally:
            # ASGI completion includes body delivery and awaited background work;
            # response_start is recorded separately from preparation completion.
            record_planner_phase(
                "request_complete",
                time.perf_counter() - start_time,
                result=request_result,
            )

    def _log_request(self, request: Request, request_id: str) -> None:
        client_ip = self._get_client_ip(request)
        user_id = self._get_user_id(request)
        user_str = f" user={user_id}" if user_id else ""
        logger.info(
            f"[REQ-{request_id}] {request.method} {request.url.path}"
            f" client={client_ip}{user_str}"
        )

    def _log_response(
        self,
        request: Request,
        status_code: int,
        request_id: str,
        elapsed: float,
    ) -> None:
        log_level = logging.INFO
        if elapsed > self.SLOW_REQUEST_THRESHOLD_SECONDS or status_code == 429:
            log_level = logging.WARNING
        # 5xx → WARNING here (outcome indicator only); the authoritative root-cause
        # ERROR is owned by the global exception handler or handle_exception().
        if status_code >= 500:
            log_level = logging.WARNING
        logger.log(
            log_level,
            f"[RES-{request_id}] {request.method} {request.url.path}"
            f" status={status_code} elapsed={elapsed:.3f}s",
        )

    def _log_error(
        self,
        request: Request,
        request_id: str,
        elapsed: float,
        error: Exception,
    ) -> None:
        # WARNING only — the authoritative root-cause ERROR is owned by
        # _unexpected_exception_handler (global handler). ServerErrorMiddleware
        # re-raises after calling the handler, so this path fires every time;
        # logging at WARNING avoids a duplicate ERROR/Sentry issue per request.
        logger.warning(
            f"[ERR-{request_id}] {request.method} {request.url.path}"
            f" elapsed={elapsed:.3f}s error={type(error).__name__}"
        )

    def _get_client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client:
            return request.client.host
        return "unknown"

    def _get_user_id(self, request: Request) -> str | None:
        try:
            return getattr(request.state, "user_id", None)
        except AttributeError:
            return None


def get_request_id(request: Request) -> str | None:
    """
    Get request ID from request state.

    Usage in route handlers:
        @router.get("/example")
        def example(request: Request):
            request_id = get_request_id(request)
    """
    try:
        return getattr(request.state, "request_id", None)
    except AttributeError:
        return None
