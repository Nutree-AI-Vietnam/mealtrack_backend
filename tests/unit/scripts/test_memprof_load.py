"""Guards for the staging memory-profile load script."""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "memprof_load.py"
_SPEC = importlib.util.spec_from_file_location("memprof_load", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_refuses_production_health_payload():
    with pytest.raises(RuntimeError, match="production"):
        _MODULE.ensure_safe_target({"deployment": {"environment": "production"}})


def test_refuses_missing_environment():
    with pytest.raises(RuntimeError, match="non-production"):
        _MODULE.ensure_safe_target({"status": "healthy"})


def test_allows_staging_environment():
    assert (
        _MODULE.ensure_safe_target({"deployment": {"environment": "staging"}})
        == "staging"
    )


def test_load_covers_groceries_search_and_chat():
    calls = _MODULE.build_load_calls(
        "plan-1",
        cycles=2,
        include_chat_post=True,
    )
    paths = [call["path"] for call in calls]
    assert "/v1/meal-plans/current" in paths
    assert "/v1/meal-plans/plan-1/groceries" in paths
    assert "/v1/foods/search" in paths
    assert paths.count("/v1/chat") == 2
    assert paths[-1] == "/v1/chat/messages"
    current = next(call for call in calls if call["path"] == "/v1/meal-plans/current")
    assert current["params"]["auto_generate"] == "false"
    search = [call for call in calls if call["path"] == "/v1/foods/search"]
    assert search[0]["params"]["q"] != search[1]["params"]["q"]
