import importlib.util
from pathlib import Path

import pytest

MIGRATIONS = (
    (
        "20260716000001_add_body_fat_visual_profiles.py",
        {"body_fat_visual_profiles": {"id"}},
    ),
    (
        "20260718000001_add_target_revisions.py",
        {
            "user_profiles": {"profile_target_revision"},
            "weekly_macro_budgets": {"target_revision"},
        },
    ),
    (
        "20260726000001_add_body_fat_visual_start_range.py",
        {"body_fat_visual_profiles": {"start_range_id"}},
    ),
    (
        "20260727000001_add_meal_recommendation_candidate_lifecycle.py",
        {},
    ),
    (
        "20260730102158384558_ensure_food_item_serving_options.py",
        {"food_item": {"serving_options"}},
    ),
)


class _Inspector:
    def __init__(self, schema: dict[str, set[str]]) -> None:
        self._schema = schema

    def has_table(self, table_name: str) -> bool:
        return table_name in self._schema

    def get_columns(self, table_name: str) -> list[dict[str, str]]:
        return [{"name": name} for name in self._schema[table_name]]


class _Operations:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_bind(self) -> object:
        return object()

    def __getattr__(self, name: str):
        def operation(*_args, **_kwargs) -> None:
            self.calls.append(name)

        return operation


@pytest.mark.parametrize(("filename", "schema"), MIGRATIONS)
def test_upgrade_skips_ddl_when_legacy_body_fat_schema_exists(
    filename: str, schema: dict[str, set[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = Path("migrations/versions") / filename
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    operations = _Operations()
    monkeypatch.setattr(module, "op", operations)
    monkeypatch.setattr(module.sa, "inspect", lambda _bind: _Inspector(schema))

    module.upgrade()

    assert operations.calls == []


def test_serving_options_repair_adds_missing_legacy_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filename = "20260730102158384558_ensure_food_item_serving_options.py"
    path = Path("migrations/versions") / filename
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    operations = _Operations()
    monkeypatch.setattr(module, "op", operations)
    monkeypatch.setattr(
        module.sa,
        "inspect",
        lambda _bind: _Inspector({"food_item": {"id"}}),
    )

    module.upgrade()

    assert operations.calls == ["add_column"]


def test_serving_options_repair_downgrade_preserves_existing_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filename = "20260730102158384558_ensure_food_item_serving_options.py"
    path = Path("migrations/versions") / filename
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    operations = _Operations()
    monkeypatch.setattr(module, "op", operations)
    monkeypatch.setattr(
        module.sa,
        "inspect",
        lambda _bind: _Inspector({"food_item": {"id", "serving_options"}}),
    )

    module.downgrade()

    assert operations.calls == []
