"""WEEKLY_RECAP_CONFIG_JSON parsing: overrides apply, bad values never do."""

from __future__ import annotations

import logging

import pytest

from src.domain.services.weekly_recap import RecapConfig


@pytest.mark.parametrize("raw", [None, "", "   ", "not json", "[1, 2]", '"text"'])
def test_empty_or_invalid_json_means_defaults(raw: str | None) -> None:
    assert RecapConfig.from_json(raw) == RecapConfig()


def test_overrides_fields_by_name() -> None:
    config = RecapConfig.from_json(
        '{"max_cards": 2, "under_ratio": 0.8, "late_snack_hour": 21.0}'
    )

    assert config.max_cards == 2
    assert config.under_ratio == 0.8
    assert config.late_snack_hour == 21
    assert isinstance(config.late_snack_hour, int)


def test_whole_numbers_become_floats_for_ratio_fields() -> None:
    config = RecapConfig.from_mapping({"under_ratio": 1})

    assert config.under_ratio == 1.0
    assert isinstance(config.under_ratio, float)


@pytest.mark.parametrize(
    "value", [True, "2", -1, 2.5, float("nan"), float("inf"), None, [2]]
)
def test_bad_values_keep_the_default(value: object) -> None:
    assert RecapConfig.from_mapping({"max_cards": value}).max_cards == 3


def test_unknown_and_bad_keys_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        config = RecapConfig.from_mapping({"max_cardz": 2, "max_cards": "2"})

    assert config == RecapConfig()
    assert "max_cardz" in caplog.text
    assert "max_cards" in caplog.text


def test_priorities_keep_only_whole_non_negative_numbers() -> None:
    config = RecapConfig.from_mapping(
        {"priorities": {"water_low": 100, "fiber_low": -1, "sugary": True, "x": "5"}}
    )

    assert dict(config.priorities) == {"water_low": 100}
    assert config.pri("water_low", 80) == 100
    assert config.pri("fiber_low", 90) == 90


def test_case_limits_keep_only_valid_entries() -> None:
    config = RecapConfig.from_mapping(
        {"cases": {"steady": {"grow": 1, "advice": "2"}, "tough": 3}}
    )

    assert {key: dict(value) for key, value in config.cases.items()} == {
        "steady": {"grow": 1}
    }
    assert config.case_limit("steady", "grow", 3) == 1
    assert config.case_limit("steady", "advice", 3) == 3
    assert config.case_limit("tough", "grow", 2) == 2


def test_overrides_that_are_not_objects_are_dropped() -> None:
    config = RecapConfig.from_mapping({"priorities": [1], "cases": "steady"})

    assert dict(config.priorities) == {}
    assert dict(config.cases) == {}
