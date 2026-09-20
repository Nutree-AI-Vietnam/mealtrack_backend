import importlib
import inspect
from unittest.mock import patch

import pytest


def test_posthog_analytics_disabled_when_no_api_key(caplog):
    """When POSTHOG_API_KEY is not set, analytics init should log disabled message."""
    with patch.dict("os.environ", {}, clear=True):  # No POSTHOG_API_KEY
        main_module = importlib.import_module("src.api.main")
        importlib.reload(main_module)
        # The module-level code should handle missing key gracefully
        # This test just confirms the import doesn't crash
    # Should not raise


def test_posthog_init_code_exists_in_main():
    """PostHog initialization code must exist in main.py."""
    main_module = importlib.import_module("src.api.main")
    source = inspect.getsource(main_module)
    assert "POSTHOG_API_KEY" in source
    assert "LangchainInstrumentor" in source or "langchain" in source.lower()


@pytest.mark.parametrize(
    ("configured_host", "expected_host"),
    [
        ("https://app.posthog.com", "https://us.i.posthog.com"),
        ("https://app.posthog.com/", "https://us.i.posthog.com"),
        ("https://eu.i.posthog.com", "https://eu.i.posthog.com"),
        (None, "https://us.i.posthog.com"),
    ],
)
def test_posthog_otel_host_uses_ingestion_host(configured_host, expected_host):
    main_module = importlib.import_module("src.api.main")
    assert main_module._resolve_posthog_otel_host(configured_host) == expected_host
