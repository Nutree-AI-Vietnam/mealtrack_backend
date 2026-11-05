import pytest
from pydantic import ValidationError

from src.infra.config.settings import Settings


def test_transport_defaults_to_cloudflare_queue(monkeypatch):
    monkeypatch.delenv("INTEGRATION_EVENT_TRANSPORT", raising=False)

    assert Settings(_env_file=None).INTEGRATION_EVENT_TRANSPORT == "cloudflare_queue"


def test_transport_reads_in_process_from_env(monkeypatch):
    monkeypatch.setenv("INTEGRATION_EVENT_TRANSPORT", "in_process")

    assert Settings(_env_file=None).INTEGRATION_EVENT_TRANSPORT == "in_process"


def test_unknown_transport_fails_at_startup():
    with pytest.raises(ValidationError):
        Settings(INTEGRATION_EVENT_TRANSPORT="redis_stream", _env_file=None)
