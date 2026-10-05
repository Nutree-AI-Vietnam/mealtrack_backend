"""Bounded planner phase timings; payloads and identifiers are never metric tags."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter

from src.observability import distribution_metric, log_event

PHASES = frozenset(
    {
        "auth",
        "auth_lookup",
        "budget",
        "checkout",
        "plan_sql",
        "catalog_sql",
        "mapping",
        "selection_cpu",
        "lock",
        "commit",
        "redis",
        "localization",
        "ai_attempt",
        "admission",
        "background",
        "response_start",
        "request_complete",
        "service",
        "response_projection",
    }
)
OPERATIONS = frozenset(
    {
        "current",
        "generate",
        "plan_update",
        "groceries",
        "grocery_update",
        "slot_log",
        "ai_prompt",
        "recipes",
        "recipe_detail",
        "recipe_enrich",
    }
)
_context: ContextVar[tuple[str, str | None] | None] = ContextVar(
    "planner_metrics_context", default=None
)


def planner_operation(path: str, method: str) -> str | None:
    """Map known URL shapes to fixed labels, discarding all resource IDs."""
    parts = path.strip("/").split("/")
    if parts[:2] == ["v1", "recipes"]:
        if len(parts) == 2 and method == "GET":
            return "recipes"
        if len(parts) == 3 and method == "GET":
            return "recipe_detail"
        if len(parts) == 5 and parts[3:] == ["micronutrients", "enrich"]:
            return "recipe_enrich" if method == "POST" else None
    if parts[:2] != ["v1", "meal-plans"]:
        return None
    if len(parts) == 3:
        if parts[2] == "current" and method == "GET":
            return "current"
        if parts[2] == "generate" and method == "POST":
            return "generate"
        return "plan_update" if method == "PATCH" else None
    if len(parts) == 4 and parts[3] == "groceries":
        return {"GET": "groceries", "PATCH": "grocery_update"}.get(method)
    if len(parts) == 4 and parts[3] == "ai-prompt" and method == "POST":
        return "ai_prompt"
    if len(parts) == 6 and parts[3] == "slots" and parts[5] == "log":
        return "slot_log" if method == "POST" else None
    return None


@contextmanager
def planner_request_context(operation: str | None, request_id: str):
    """Scope attribution to this request, also inherited by its background tasks."""
    if operation is not None and operation not in OPERATIONS:
        raise ValueError("Unknown planner operation")
    token = _context.set((operation, request_id) if operation else None)
    try:
        yield
    finally:
        _context.reset(token)


def record_planner_phase(phase: str, elapsed: float, *, result: str = "success"):
    """Record a monotonic duration. Connector failure cannot fail a request."""
    if phase not in PHASES or result not in {"success", "error", "cancelled"}:
        raise ValueError("Unknown planner phase or result")
    context = _context.get()
    if context is None:
        return
    operation, request_id = context
    attributes = {"operation": operation, "phase": phase, "result": result}
    try:
        distribution_metric(
            "planner.phase.duration",
            max(0.0, elapsed) * 1000,
            unit="millisecond",
            attributes=attributes,
        )
        log_event(
            "debug",
            "planner phase completed",
            attributes={
                **attributes,
                "elapsed_ms": max(0.0, elapsed) * 1000,
                "request_id": request_id,
            },
        )
    except Exception:
        # Metrics are optional; never report a provider payload or exception text.
        pass


@contextmanager
def planner_phase(phase: str):
    """Use `with planner_phase(...)` across an await or synchronous CPU work."""
    if phase not in PHASES:
        raise ValueError("Unknown planner phase")
    start = perf_counter()
    result = "success"
    try:
        yield
    except BaseException as error:
        result = "cancelled" if isinstance(error, asyncio.CancelledError) else "error"
        raise
    finally:
        record_planner_phase(phase, perf_counter() - start, result=result)


def planner_timed(phase: str):
    """Instrument an async orchestration boundary without changing its signature."""
    from functools import wraps

    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            with planner_phase(phase):
                return await function(*args, **kwargs)

        return wrapped

    return decorate
