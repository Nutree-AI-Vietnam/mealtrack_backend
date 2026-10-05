def test_neutral_translation_getter_fails_open_without_openai_key(monkeypatch):
    import src.api.base_dependencies as deps

    monkeypatch.setattr(deps, "_text_translation_service", None)
    monkeypatch.setattr(deps.settings, "OPENAI_API_KEY", None)

    assert deps.get_text_translation_service() is None


def test_neutral_translation_getter_is_process_scoped(monkeypatch):
    import src.api.base_dependencies as deps

    class _Provider:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _Adapter:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(deps, "_text_translation_service", None)
    monkeypatch.setattr(deps.settings, "OPENAI_API_KEY", "test-key")

    import src.infra.adapters.openai_translation_adapter as adapter_module
    import src.infra.services.ai.providers.openai_provider as provider_module

    monkeypatch.setattr(provider_module, "OpenAIProvider", _Provider)
    monkeypatch.setattr(adapter_module, "OpenAITranslationAdapter", _Adapter)

    first = deps.get_text_translation_service()
    second = deps.get_text_translation_service()

    assert first is second
    assert first is not None


def test_planner_translation_always_reads_persisted_overlays(monkeypatch):
    import src.api.base_dependencies as deps
    from src.app.services.catalog_persisted_presentation_service import (
        CatalogPersistedPresentationService,
    )

    monkeypatch.delenv("CATALOG_PERSISTED_PRESENTATION_ENABLED", raising=False)
    monkeypatch.setattr(deps.settings, "OPENAI_API_KEY", "test-key")

    assert isinstance(
        deps.get_planner_translation_service(), CatalogPersistedPresentationService
    )
