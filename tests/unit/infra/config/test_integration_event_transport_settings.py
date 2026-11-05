import pytest
from pydantic import ValidationError

from src.infra.config.settings import Settings


def test_transport_defaults_to_cloudflare_queue(monkeypatch):
    monkeypatch.delenv("INTEGRATION_EVENT_TRANSPORT", raising=False)

    settings = Settings(_env_file=None)

    assert settings.INTEGRATION_EVENT_TRANSPORT == "cloudflare_queue"
    assert settings.INTEGRATION_EVENT_STREAM_MAXLEN == 20_000


def test_transport_reads_redis_stream_from_env(monkeypatch):
    monkeypatch.setenv("INTEGRATION_EVENT_TRANSPORT", "redis_stream")

    settings = Settings(_env_file=None)

    assert settings.INTEGRATION_EVENT_TRANSPORT == "redis_stream"


def test_unknown_transport_fails_at_startup():
    with pytest.raises(ValidationError):
        Settings(INTEGRATION_EVENT_TRANSPORT="redis-stream", _env_file=None)


def test_stream_maxlen_must_be_positive():
    with pytest.raises(ValidationError):
        Settings(INTEGRATION_EVENT_STREAM_MAXLEN=0, _env_file=None)
