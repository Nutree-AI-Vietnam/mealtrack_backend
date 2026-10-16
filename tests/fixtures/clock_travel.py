"""Opt-in pytest plugin: run the session with the wall clock moved forward.

    PYTEST_CLOCK_TRAVEL_DAYS=400 pytest -p tests.fixtures.clock_travel <tests>

Catches tests that pin dates (e.g. the fixed ``NOW`` in the billing fakes)
while the code under test reads the real clock: such a test passes today and
fails once the calendar passes its pinned dates. CI runs the entitlement suites
this way. Without the variable the plugin does nothing.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

TRAVEL_DAYS_ENV = "PYTEST_CLOCK_TRAVEL_DAYS"

_traveller = None


def pytest_configure(config: pytest.Config) -> None:
    global _traveller
    days = os.environ.get(TRAVEL_DAYS_ENV, "").strip()
    if not days:
        return
    import time_machine

    destination = datetime.now(UTC) + timedelta(days=float(days))
    # tick=True keeps time moving, so timeouts and orderings still behave.
    _traveller = time_machine.travel(destination, tick=True)
    _traveller.start()


def pytest_unconfigure(config: pytest.Config) -> None:
    global _traveller
    if _traveller is not None:
        _traveller.stop()
        _traveller = None


def pytest_report_header(config: pytest.Config) -> str | None:
    if _traveller is None:
        return None
    return f"clock_travel: wall clock is {datetime.now(UTC).isoformat()}"
