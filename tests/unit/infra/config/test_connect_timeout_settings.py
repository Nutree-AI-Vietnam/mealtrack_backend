"""ASYNC_CONNECT_TIMEOUT must not crash Settings on blank or junk values."""

import pytest

from src.infra.config.settings import Settings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", None),
        ("0", 10),
        ("-5", 10),
        ("abc", 10),
        ("4", 4),
    ],
)
def test_settings_connect_timeout_blank_and_invalid(raw, expected):
    settings = Settings(ASYNC_CONNECT_TIMEOUT=raw, _env_file=None)
    assert settings.ASYNC_CONNECT_TIMEOUT == expected


def test_settings_connect_timeout_defaults_to_unset():
    settings = Settings(_env_file=None)
    assert settings.ASYNC_CONNECT_TIMEOUT is None
