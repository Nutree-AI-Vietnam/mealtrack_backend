"""Read-only load must never invoke the released implicit-generation default."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest

_spec = spec_from_file_location(
    "weekly_planner_load",
    Path(__file__).resolve().parents[2] / "scripts/benchmarks/weekly_planner_load.py",
)
_module = module_from_spec(_spec)
_spec.loader.exec_module(_module)
validate = _module.validate


def _validate(
    monkeypatch, path, *, mutations=False, group="current", method="GET", headers=None
):
    monkeypatch.setenv("PLANNER_DEDICATED_TEST_TOKEN", "dedicated-fixture-token")
    args = SimpleNamespace(
        base_url="http://127.0.0.1:8000",
        dedicated_test_target=False,
        users=1,
        seconds=1,
        allow_test_mutations=mutations,
    )
    return validate(
        args,
        {
            "accounts": [
                {
                    "token_env": "PLANNER_DEDICATED_TEST_TOKEN",
                    "workflows": [
                        {
                            "group": group,
                            "steps": [
                                {
                                    "path": path,
                                    "method": method,
                                    "headers": headers or {},
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    )


@pytest.mark.parametrize(
    "path",
    [
        "/v1/meal-plans/current",
        "/v1/meal-plans/current?note=auto_generate=false",
        "/v1/meal-plans/current?auto_generate=false&auto_generate=true",
        "/v1/meal-plans/current?auto_generate=false&auto_generate=false",
    ],
)
def test_read_only_load_rejects_implicit_or_ambiguous_generation(monkeypatch, path):
    with pytest.raises(ValueError):
        _validate(monkeypatch, path)


def test_read_only_explicit_false_is_accepted(monkeypatch):
    assert _validate(
        monkeypatch,
        "/v1/meal-plans/current?auto_generate=false&include_grocery_count=false",
    )


@pytest.mark.parametrize(
    "path,headers",
    [
        ("/v1/users/account", {}),
        ("/v1/meal-plans/current#fragment", {}),
        ("/v1/meal-plans/current", {"X-HTTP-Method-Override": "DELETE"}),
    ],
)
def test_target_must_match_planner_group_and_safe_method(monkeypatch, path, headers):
    with pytest.raises(ValueError):
        _validate(monkeypatch, path, mutations=True, headers=headers)
