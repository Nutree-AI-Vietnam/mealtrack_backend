"""Unit-test defaults shared by every package under ``tests/unit``."""

import pytest
from tests.fixtures.fakes.fake_feature_flag_reader import StaticFeatureFlagReader


@pytest.fixture(autouse=True)
def _entitlement_flags_off(monkeypatch):
    """Server kill switches read as off without touching a database.

    The process-wide reader would otherwise query ``feature_flags`` through the
    default engine. Tests that need a flag on patch their own reader.
    """
    from src.bootstrap import entitlements

    reader = StaticFeatureFlagReader()
    monkeypatch.setattr(entitlements, "_feature_flag_reader", reader)
    return reader
