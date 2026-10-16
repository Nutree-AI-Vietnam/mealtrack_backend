"""Shared fixtures for the NM-680 billing domain tests."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

# RevenueCat v1 GET /subscribers bodies, dated against 2026-10-15T08:00:00Z.
RC_V1_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "revenuecat_v1"


@pytest.fixture
def rc_body() -> Callable[[str], dict[str, Any]]:
    def load(name: str) -> dict[str, Any]:
        return json.loads((RC_V1_FIXTURES / f"{name}.json").read_text())

    return load
