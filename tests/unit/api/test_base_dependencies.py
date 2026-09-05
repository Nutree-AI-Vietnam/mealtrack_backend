import pytest

from src.api.base_dependencies import get_gpt_parser
from src.domain.parsers.gpt_response_parser import GPTResponseParser


@pytest.mark.asyncio
async def test_initialize_cache_layer_keeps_redis_for_provider_budget_when_cache_disabled(
    monkeypatch,
):
    import src.api.base_dependencies as dependencies

    class _Redis:
        def __init__(self, redis_url, max_connections):
            self.redis_url = redis_url
            self.max_connections = max_connections
            self.connected = False

        async def connect(self):
            self.connected = True

        async def disconnect(self):
            self.connected = False

    monkeypatch.setattr(dependencies, "RedisClient", _Redis)
    monkeypatch.setattr(dependencies, "_redis_client", None)
    monkeypatch.setattr(dependencies, "_cache_service", None)
    monkeypatch.setattr(dependencies.settings, "CACHE_ENABLED", False)
    monkeypatch.setattr(dependencies.settings, "NUTRITION_PROVIDER_GLOBAL_RPM", 10)

    await dependencies.initialize_cache_layer()

    assert dependencies.get_cache_service() is not None
    assert dependencies.get_cache_service().enabled is False
    assert dependencies._redis_client.connected is True

    await dependencies.shutdown_cache_layer()


def test_get_parse_text_settings_reads_structured_reference_flag(monkeypatch):
    import src.api.base_dependencies as dependencies

    class _Settings:
        PARSE_TEXT_STRUCTURED_REFERENCE_ENABLED = True
        PARSE_TEXT_PURE_AI_ENABLED = False

    import src.infra.config.settings as settings_module

    monkeypatch.setattr(settings_module, "get_settings", lambda: _Settings())

    assert dependencies.get_parse_text_settings() == {
        "structured_reference_enabled": True,
        "pure_ai_mode": False,
    }


def test_get_gpt_parser_returns_parser_instance():
    parser = get_gpt_parser()
    assert isinstance(parser, GPTResponseParser)
