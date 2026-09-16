import inspect


def test_nutrition_singletons_use_async_food_reference_adapter(monkeypatch):
    import src.api.base_dependencies as deps

    class _Resolver:
        def __init__(self, fatsecret, food_ref_repo):
            self.food_ref_repo = food_ref_repo

    class _NutritionLookup:
        def __init__(
            self,
            food_ref_repo,
            ingredient_nutrition_resolver,
            generation_service,
            redis_client=None,
            cache_service=None,
        ):
            self.food_ref_repo = food_ref_repo
            self.ingredient_nutrition_resolver = ingredient_nutrition_resolver

    class _MealGenerationService:
        pass

    class _AsyncFoodReference:
        async def find_batch_by_normalized_names(self, names_normalized):
            return {}

        async def upsert_by_normalized_name(self, **kwargs):
            return None

    deps._ingredient_nutrition_resolver = None
    deps._nutrition_lookup_service = None
    monkeypatch.setattr(deps, "_async_food_reference_repository", _AsyncFoodReference())
    monkeypatch.setattr(deps, "get_fat_secret_service_instance", lambda: object())

    import src.domain.services.meal_suggestion.ingredient_nutrition_resolver as resolver_mod
    import src.domain.services.meal_suggestion.nutrition_lookup_service as lookup_mod
    import src.infra.adapters.meal_generation_service as meal_gen_mod

    monkeypatch.setattr(resolver_mod, "IngredientNutritionResolver", _Resolver)
    monkeypatch.setattr(lookup_mod, "NutritionLookupService", _NutritionLookup)
    monkeypatch.setattr(meal_gen_mod, "MealGenerationService", _MealGenerationService)

    resolver = deps.get_ingredient_nutrition_resolver()
    lookup = deps.get_nutrition_lookup_service()

    assert resolver.food_ref_repo is deps._async_food_reference_repository
    assert lookup.food_ref_repo is deps._async_food_reference_repository
    assert lookup.ingredient_nutrition_resolver is resolver
    assert inspect.iscoroutinefunction(
        lookup.food_ref_repo.find_batch_by_normalized_names
    )


def test_legacy_food_reference_getter_returns_async_adapter(monkeypatch):
    import src.api.base_dependencies as deps

    class _AsyncFoodReference:
        async def find_batch_by_normalized_names(self, names_normalized):
            return {}

    deps._async_food_reference_repository = _AsyncFoodReference()

    repo = deps.get_food_reference_repository()

    assert repo is deps._async_food_reference_repository
    assert inspect.iscoroutinefunction(repo.find_batch_by_normalized_names)


def test_text_translation_singleton_uses_openai_adapter(monkeypatch):
    import src.api.base_dependencies as deps
    from src.domain.services.translation.text_translation_service import (
        TextTranslationService,
    )
    from src.infra.adapters.openai_translation_adapter import OpenAITranslationAdapter

    monkeypatch.setattr(deps, "_text_translation_service", None)
    monkeypatch.setattr(deps.settings, "OPENAI_API_KEY", "test-key")

    service = deps.get_text_translation_service()

    assert isinstance(service, TextTranslationService)
    assert isinstance(service._port, OpenAITranslationAdapter)
    assert service._port._model == deps.settings.OPENAI_TRANSLATION_MODEL
