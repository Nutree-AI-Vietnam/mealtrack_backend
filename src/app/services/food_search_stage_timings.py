"""Per-stage latency for one food search; never carries the query text."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter

from src.observability import distribution_metric


class FoodSearchStageTimings:
    def __init__(self) -> None:
        self._stages: list[tuple[str, float]] = []

    @contextmanager
    def measure(self, stage: str) -> Iterator[None]:
        started = perf_counter()
        try:
            yield
        finally:
            self._stages.append((stage, (perf_counter() - started) * 1000))

    def emit(self, *, language: str, mode: str) -> None:
        for stage, elapsed_ms in self._stages:
            distribution_metric(
                "food_search.stage.latency_ms",
                elapsed_ms,
                unit="millisecond",
                attributes={"stage": stage, "language": language, "mode": mode},
            )
        self._stages.clear()
