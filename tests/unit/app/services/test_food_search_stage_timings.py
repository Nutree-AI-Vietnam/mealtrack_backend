"""Per-stage food search latency metrics."""

import pytest

from src.app.services import food_search_stage_timings
from src.app.services.food_search_stage_timings import FoodSearchStageTimings


@pytest.fixture
def emitted(monkeypatch):
    calls = []

    def record(name, value, *, unit=None, attributes=None):
        calls.append((name, value, unit, attributes))

    monkeypatch.setattr(food_search_stage_timings, "distribution_metric", record)
    return calls


def test_emit_reports_each_stage_in_order(emitted):
    timings = FoodSearchStageTimings()
    with timings.measure("local"):
        pass
    with timings.measure("provider"):
        pass

    timings.emit(language="vi", mode="search")

    assert [(name, unit, attributes) for name, _, unit, attributes in emitted] == [
        (
            "food_search.stage.latency_ms",
            "millisecond",
            {"stage": "local", "language": "vi", "mode": "search"},
        ),
        (
            "food_search.stage.latency_ms",
            "millisecond",
            {"stage": "provider", "language": "vi", "mode": "search"},
        ),
    ]
    assert all(value >= 0 for _, value, _, _ in emitted)


def test_emit_reports_stages_only_once(emitted):
    timings = FoodSearchStageTimings()
    with timings.measure("local"):
        pass

    timings.emit(language="en", mode="autocomplete")
    timings.emit(language="en", mode="autocomplete")

    assert len(emitted) == 1


def test_failed_stage_is_still_measured(emitted):
    timings = FoodSearchStageTimings()

    with pytest.raises(RuntimeError):
        with timings.measure("provider"):
            raise RuntimeError("provider down")
    timings.emit(language="en", mode="search")

    assert [attributes["stage"] for _, _, _, attributes in emitted] == ["provider"]
