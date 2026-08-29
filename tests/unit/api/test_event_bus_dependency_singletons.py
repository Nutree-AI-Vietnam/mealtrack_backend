import importlib

import pytest


class _CacheStub:
    redis = object()


def test_provider_budget_does_not_depend_on_optional_cache_flag(monkeypatch):
    mod = importlib.import_module("src.api.dependencies.event_bus")

    class _Cache:
        redis = object()

    class _Budget:
        def __init__(self, redis):
            self.redis = redis

    monkeypatch.setattr(mod, "RedisProviderBudget", _Budget)
    monkeypatch.setattr(mod.settings, "CACHE_ENABLED", False)
    monkeypatch.setattr(mod.settings, "NUTRITION_PROVIDER_GLOBAL_RPM", 10)

    budget = mod._build_provider_budget(_Cache())

    assert isinstance(budget, _Budget)


def test_development_uses_memory_budget_when_redis_cache_missing(monkeypatch):
    mod = importlib.import_module("src.api.dependencies.event_bus")
    monkeypatch.setattr(mod.settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(mod.settings, "NUTRITION_PROVIDER_GLOBAL_RPM", 10)

    budget = mod._build_provider_budget(None)

    assert isinstance(budget, mod.MemoryProviderBudget)


def test_get_food_search_event_bus_is_singleton(monkeypatch):
    # Import fresh to reset module globals
    mod = importlib.import_module("src.api.dependencies.event_bus")
    monkeypatch.setattr(mod, "_food_search_event_bus", None, raising=False)

    # Patch PyMediatorEventBus to lightweight stub so we don't pull heavy deps
    class _Bus:
        def __init__(self):
            self.registered = []

        def register_handler(self, *args, **kwargs):
            self.registered.append((args, kwargs))

    monkeypatch.setattr(mod, "PyMediatorEventBus", _Bus)

    # Patch base dependency getters imported inside get_food_search_event_bus()
    import src.api.base_dependencies as deps

    monkeypatch.setattr(deps, "get_food_cache_service", lambda: object())
    monkeypatch.setattr(deps, "get_food_data_service", lambda: object())
    monkeypatch.setattr(deps, "get_food_mapping_service", lambda: object())
    monkeypatch.setattr(deps, "get_open_food_facts_service_instance", lambda: object())
    monkeypatch.setattr(deps, "get_fat_secret_service_instance", lambda: object())
    monkeypatch.setattr(deps, "get_food_reference_repository", lambda: object())

    # Patch MealGenerationService + translation service referenced in function.
    # These are locally imported inside get_food_search_event_bus(), so we must
    # patch the source module, not the event_bus module attribute.
    class _MealGen:
        pass

    class _Translator:
        def __init__(self, *args, **kwargs):
            pass

    import src.infra.adapters.meal_generation_service as _meal_gen_mod

    monkeypatch.setattr(_meal_gen_mod, "MealGenerationService", _MealGen)
    monkeypatch.setattr(mod, "FoodSearchTranslationService", _Translator, raising=False)

    # Patch handlers to avoid constructing real ones
    monkeypatch.setattr(mod, "SearchFoodsQueryHandler", lambda *a, **k: object())
    monkeypatch.setattr(mod, "GetPopularStaplesQueryHandler", lambda *a, **k: object())
    monkeypatch.setattr(mod, "GetFoodDetailsQueryHandler", lambda *a, **k: object())
    monkeypatch.setattr(
        mod, "GetProviderFoodDetailsQueryHandler", lambda *a, **k: object()
    )
    monkeypatch.setattr(mod, "LookupBarcodeQueryHandler", lambda *a, **k: object())

    bus1 = mod.get_food_search_event_bus()
    bus2 = mod.get_food_search_event_bus()
    assert bus1 is bus2
    assert len(bus1.registered) == 5


def test_get_configured_event_bus_is_singleton(monkeypatch):
    mod = importlib.import_module("src.api.dependencies.event_bus")
    monkeypatch.setattr(mod, "_configured_event_bus", None, raising=False)

    class _Bus:
        def __init__(self):
            self.handlers = []
            self.subscriptions = []

        def register_handler(self, *args, **kwargs):
            self.handlers.append((args, kwargs))

        def subscribe(self, *args, **kwargs):
            self.subscriptions.append((args, kwargs))

    monkeypatch.setattr(mod, "PyMediatorEventBus", _Bus)

    # Patch base dependency getters imported inside get_configured_event_bus()
    import src.api.base_dependencies as deps

    monkeypatch.setattr(deps, "get_image_store", lambda: object())
    monkeypatch.setattr(deps, "get_vision_service", lambda: object())
    monkeypatch.setattr(deps, "get_gpt_parser", lambda: object())
    monkeypatch.setattr(deps, "get_food_cache_service", lambda: object())
    monkeypatch.setattr(deps, "get_food_data_service", lambda: object())
    monkeypatch.setattr(deps, "get_food_mapping_service", lambda: object())
    monkeypatch.setattr(deps, "get_fat_secret_service_instance", lambda: object())
    monkeypatch.setattr(deps, "get_cache_service", lambda: _CacheStub())
    monkeypatch.setattr(deps, "get_suggestion_orchestration_service", lambda: object())
    monkeypatch.setattr(deps, "get_meal_translation_service", lambda: object())

    class _MealGen:
        pass

    import src.infra.adapters.meal_generation_service as _meal_gen_mod

    monkeypatch.setattr(_meal_gen_mod, "MealGenerationService", _MealGen)

    # Patch every handler class referenced to cheap stubs.
    # They're imported in the module, so patching attributes is enough.
    handler_names = [
        "UploadMealImageImmediatelyHandler",
        "EditMealCommandHandler",
        "AddCustomIngredientCommandHandler",
        "DeleteMealCommandHandler",
        "DeleteMealPhotoCommandHandler",
        "CreateManualMealCommandHandler",
        "ParseMealTextHandler",
        "SearchFoodsQueryHandler",
        "GetFoodDetailsQueryHandler",
        "GetMealByIdQueryHandler",
        "GetDailyMacrosQueryHandler",
        "GetWeeklyBudgetQueryHandler",
        "GetStreakQueryHandler",
        "GetDailyBreakdownQueryHandler",
        "GetDailyActivitiesQueryHandler",
        "GenerateDailyMealSuggestionsCommandHandler",
        "GenerateSingleMealCommandHandler",
        "GetMealSuggestionsForProfileQueryHandler",
        "GetSingleMealForProfileQueryHandler",
        "GetMealPlanningSummaryQueryHandler",
        "GetMealsByDateQueryHandler",
        "SaveMealSuggestionCommandHandler",
        "SaveUserOnboardingCommandHandler",
        "SyncUserCommandHandler",
        "UpdateUserLastAccessedCommandHandler",
        "CompleteOnboardingCommandHandler",
        "DeleteUserCommandHandler",
        "UpdateUserMetricsCommandHandler",
        "UpdateTimezoneCommandHandler",
        "UpdateCustomMacrosCommandHandler",
        "GetUserProfileQueryHandler",
        "GetUserByFirebaseUidQueryHandler",
        "GetUserOnboardingStatusQueryHandler",
        "GetUserMetricsQueryHandler",
        "GetUserTdeeQueryHandler",
        "PreviewTdeeQueryHandler",
        "UpdateNotificationPreferencesCommandHandler",
        "GetNotificationPreferencesQueryHandler",
        "RecognizeIngredientCommandHandler",
        "CreateThreadCommandHandler",
        "SendMessageCommandHandler",
        "DeleteThreadCommandHandler",
        "GetThreadsQueryHandler",
        "GetThreadQueryHandler",
        "GetMessagesQueryHandler",
        "MarkCheatDayCommandHandler",
        "UnmarkCheatDayCommandHandler",
        "GetCheatDaysQueryHandler",
        "SaveSuggestionCommandHandler",
        "DeleteSavedSuggestionCommandHandler",
        "GetSavedSuggestionsQueryHandler",
    ]

    for name in handler_names:
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, lambda *a, **k: object())

    bus1 = mod.get_configured_event_bus()
    bus2 = mod.get_configured_event_bus()
    assert bus1 is bus2
    # Sanity: lots of registrations should have happened.
    assert len(bus1.handlers) > 10
    assert len(bus1.subscriptions) == 0

    from src.app.commands.meal.parse_meal_text_command import ParseMealTextCommand

    assert (
        sum(
            1
            for args, _kwargs in bus1.handlers
            if args and args[0] is ParseMealTextCommand
        )
        == 1
    )


def test_configured_event_bus_wires_meal_analyze_validation(monkeypatch):
    mod = importlib.import_module("src.api.dependencies.event_bus")
    monkeypatch.setattr(mod, "_configured_event_bus", None, raising=False)

    class _Bus:
        def __init__(self):
            self.handlers = {}

        def register_handler(self, event_type, handler):
            self.handlers[event_type] = handler

        def subscribe(self, *args, **kwargs):
            pass

    monkeypatch.setattr(mod, "PyMediatorEventBus", _Bus)

    import src.api.base_dependencies as deps

    monkeypatch.setattr(deps, "get_image_store", lambda: object())
    monkeypatch.setattr(deps, "get_vision_service", lambda: object())
    monkeypatch.setattr(deps, "get_gpt_parser", lambda: object())
    monkeypatch.setattr(deps, "get_food_cache_service", lambda: object())
    monkeypatch.setattr(deps, "get_food_data_service", lambda: object())
    monkeypatch.setattr(deps, "get_food_mapping_service", lambda: object())
    monkeypatch.setattr(deps, "get_fat_secret_service_instance", lambda: object())
    monkeypatch.setattr(deps, "get_cache_service", lambda: _CacheStub())
    monkeypatch.setattr(deps, "get_suggestion_orchestration_service", lambda: object())
    monkeypatch.setattr(deps, "get_meal_translation_service", lambda: object())
    monkeypatch.setattr(deps, "get_text_translation_service", lambda: object())

    class _Settings:
        AI_MEAL_ANALYZE_GRAPH_ENABLED = True
        AI_MEAL_ANALYZE_FATSECRET_VALIDATION_ENABLED = True
        AI_MEAL_ANALYZE_EXTERNAL_PROVIDER_TIMEOUT_SECONDS = 7.0
        AI_MEAL_ANALYZE_GRAPH_VERSION = "test-v2"
        CLOUDFLARE_QUEUE_ACCOUNT_ID = "test-account"
        CLOUDFLARE_QUEUE_ID = "testqueue"
        CLOUDFLARE_QUEUE_API_TOKEN = "test-token"
        CLOUDFLARE_QUEUE_TIMEOUT_SECONDS = 10.0

    import src.infra.config.settings as settings_mod

    monkeypatch.setattr(settings_mod, "get_settings", lambda: _Settings())

    class _MealGen:
        pass

    import src.infra.adapters.meal_generation_service as _meal_gen_mod

    monkeypatch.setattr(_meal_gen_mod, "MealGenerationService", _MealGen)

    class _StubWithHandle:
        async def handle(self, *args, **kwargs):
            return None

    handler_names = [
        "EditMealCommandHandler",
        "AddCustomIngredientCommandHandler",
        "DeleteMealCommandHandler",
        "CreateManualMealCommandHandler",
        "ParseMealTextHandler",
        "SearchFoodsQueryHandler",
        "GetFoodDetailsQueryHandler",
        "GetMealByIdQueryHandler",
        "GetDailyMacrosQueryHandler",
        "GetWeeklyBudgetQueryHandler",
        "GetStreakQueryHandler",
        "GetDailyBreakdownQueryHandler",
        "GetNutritionBulkQueryHandler",
        "GetActivitiesPresenceQueryHandler",
        "GetDailyActivitiesQueryHandler",
        "GetBulkActivitiesQueryHandler",
        "GetMealsByDateQueryHandler",
        "DiscoverMealsCommandHandler",
        "GenerateMealRecipesCommandHandler",
        "SaveMealSuggestionCommandHandler",
        "SaveUserOnboardingCommandHandler",
        "SyncUserCommandHandler",
        "UpdateUserLastAccessedCommandHandler",
        "CompleteOnboardingCommandHandler",
        "DeleteUserCommandHandler",
        "UpdateUserMetricsCommandHandler",
        "UpdateTimezoneCommandHandler",
        "UpdateLanguageCommandHandler",
        "UpdateCustomMacrosCommandHandler",
        "GetUserProfileQueryHandler",
        "GetUserByFirebaseUidQueryHandler",
        "GetUserOnboardingStatusQueryHandler",
        "GetUserMetricsQueryHandler",
        "GetUserTdeeQueryHandler",
        "PreviewTdeeQueryHandler",
        "UpdateNotificationPreferencesCommandHandler",
        "GetNotificationPreferencesQueryHandler",
        "RecognizeIngredientCommandHandler",
        "MarkCheatDayCommandHandler",
        "UnmarkCheatDayCommandHandler",
        "GetCheatDaysQueryHandler",
        "AddWeightEntryCommandHandler",
        "DeleteWeightEntryCommandHandler",
        "SyncWeightEntriesCommandHandler",
        "GetWeightEntriesQueryHandler",
        "SaveSuggestionCommandHandler",
        "DeleteSavedSuggestionCommandHandler",
        "GetSavedSuggestionsQueryHandler",
        "LogMovementCommandHandler",
        "DeleteMovementEntryCommandHandler",
        "UpdateMovementEntryCommandHandler",
        "LookupBarcodeQueryHandler",
        "AttachMealPhotoCommandHandler",
    ]

    for name in handler_names:
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, lambda *a, **k: _StubWithHandle())

    bus = mod.get_configured_event_bus()
    upload_handler = bus.handlers[mod.UploadMealImageImmediatelyCommand]
    scan_handler = bus.handlers[mod.ScanByUrlCommand]

    assert upload_handler.meal_analyze_graph_enabled is True
    assert scan_handler.meal_analyze_graph_enabled is True
    workflow = upload_handler.meal_analyze_workflow
    assert workflow is scan_handler.meal_analyze_workflow
    assert workflow._fatsecret_validation_enabled is True
    assert workflow._food_reference_validation_service is not None
    assert workflow._graph_version == "test-v2"


@pytest.mark.asyncio
async def test_configured_event_bus_can_send_movement_catalog_query(monkeypatch):
    mod = importlib.import_module("src.api.dependencies.event_bus")
    monkeypatch.setattr(mod, "_configured_event_bus", None, raising=False)

    class _Bus:
        def __init__(self):
            self.handlers = {}

        def register_handler(self, event_type, handler):
            self.handlers[event_type] = handler

        def subscribe(self, *args, **kwargs):
            pass

        async def send(self, event):
            event_type = type(event)
            if event_type not in self.handlers:
                raise ValueError(f"No handler registered for {event_type.__name__}")
            return await self.handlers[event_type].handle(event)

    monkeypatch.setattr(mod, "PyMediatorEventBus", _Bus)

    import src.api.base_dependencies as deps

    monkeypatch.setattr(deps, "get_image_store", lambda: object())
    monkeypatch.setattr(deps, "get_vision_service", lambda: object())
    monkeypatch.setattr(deps, "get_gpt_parser", lambda: object())
    monkeypatch.setattr(deps, "get_food_cache_service", lambda: object())
    monkeypatch.setattr(deps, "get_food_data_service", lambda: object())
    monkeypatch.setattr(deps, "get_food_mapping_service", lambda: object())
    monkeypatch.setattr(deps, "get_fat_secret_service_instance", lambda: object())
    monkeypatch.setattr(deps, "get_cache_service", lambda: _CacheStub())
    monkeypatch.setattr(deps, "get_suggestion_orchestration_service", lambda: object())
    monkeypatch.setattr(deps, "get_meal_translation_service", lambda: object())

    class _MealGen:
        pass

    import src.infra.adapters.meal_generation_service as _meal_gen_mod

    monkeypatch.setattr(_meal_gen_mod, "MealGenerationService", _MealGen)

    class _StubWithHandle:
        async def handle(self, *args, **kwargs):
            return None

    handler_names = [
        "UploadMealImageImmediatelyHandler",
        "EditMealCommandHandler",
        "AddCustomIngredientCommandHandler",
        "DeleteMealCommandHandler",
        "DeleteMealPhotoCommandHandler",
        "CreateManualMealCommandHandler",
        "ParseMealTextHandler",
        "SearchFoodsQueryHandler",
        "GetFoodDetailsQueryHandler",
        "GetMealByIdQueryHandler",
        "GetDailyMacrosQueryHandler",
        "GetWeeklyBudgetQueryHandler",
        "GetStreakQueryHandler",
        "GetDailyBreakdownQueryHandler",
        "GetNutritionBulkQueryHandler",
        "GetActivitiesPresenceQueryHandler",
        "GetDailyActivitiesQueryHandler",
        "GetBulkActivitiesQueryHandler",
        "GetMealsByDateQueryHandler",
        "DiscoverMealsCommandHandler",
        "GenerateMealRecipesCommandHandler",
        "SaveMealSuggestionCommandHandler",
        "SaveUserOnboardingCommandHandler",
        "SyncUserCommandHandler",
        "UpdateUserLastAccessedCommandHandler",
        "CompleteOnboardingCommandHandler",
        "DeleteUserCommandHandler",
        "UpdateUserMetricsCommandHandler",
        "UpdateTimezoneCommandHandler",
        "UpdateLanguageCommandHandler",
        "UpdateCustomMacrosCommandHandler",
        "GetUserProfileQueryHandler",
        "GetUserByFirebaseUidQueryHandler",
        "GetUserOnboardingStatusQueryHandler",
        "GetUserMetricsQueryHandler",
        "GetUserTdeeQueryHandler",
        "PreviewTdeeQueryHandler",
        "UpdateNotificationPreferencesCommandHandler",
        "GetNotificationPreferencesQueryHandler",
        "RecognizeIngredientCommandHandler",
        "MarkCheatDayCommandHandler",
        "UnmarkCheatDayCommandHandler",
        "GetCheatDaysQueryHandler",
        "AddWeightEntryCommandHandler",
        "DeleteWeightEntryCommandHandler",
        "SyncWeightEntriesCommandHandler",
        "GetWeightEntriesQueryHandler",
        "SaveSuggestionCommandHandler",
        "DeleteSavedSuggestionCommandHandler",
        "GetSavedSuggestionsQueryHandler",
    ]

    for name in handler_names:
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, lambda *a, **k: _StubWithHandle())

    from src.app.queries.movement import GetMovementCatalogQuery

    bus = mod.get_configured_event_bus()
    result = await bus.send(GetMovementCatalogQuery())

    assert result["activities"]
