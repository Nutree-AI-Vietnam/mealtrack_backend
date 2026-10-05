"""One monotonic interactive planner deadline shared across request stages."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic

_deadline: ContextVar[float | None] = ContextVar("planner_deadline", default=None)


def current_deadline() -> float | None:
    return _deadline.get()


def remaining_budget(maximum: float) -> float:
    deadline = current_deadline()
    remaining = maximum if deadline is None else min(maximum, deadline - monotonic())
    if remaining <= 0:
        raise TimeoutError("Planner request deadline expired")
    return remaining


def timeout_until(deadline: float) -> asyncio.Timeout:
    # Deadlines use time.monotonic(); event-loop clocks (e.g. uvloop) may not.
    return asyncio.timeout(max(0.0, deadline - monotonic()))


@contextmanager
def planner_deadline(deadline: float):
    token = _deadline.set(deadline)
    try:
        yield
    finally:
        _deadline.reset(token)


_retry_budget: ContextVar[int | None] = ContextVar("planner_retry_budget", default=None)


@contextmanager
def planner_retry_budget():
    token = _retry_budget.set(1)
    try:
        yield
    finally:
        _retry_budget.reset(token)


def consume_planner_retry() -> bool:
    budget = _retry_budget.get()
    if budget is None:
        return True
    if budget <= 0:
        return False
    _retry_budget.set(budget - 1)
    return True
