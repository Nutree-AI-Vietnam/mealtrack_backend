"""
Unit tests for SuggestionOrchestrationService's direct generation pipeline.
Tests the streamlined 2-phase generation process:
- Phase 1: Generation of 4 diverse meal names.
- Phase 2: Parallel generation of 4 recipes from those names.
- Direct generation in the target language is tested.
- Error handling for insufficient name/recipe generation is tested.
"""

from unittest.mock import AsyncMock, Mock

import pytest

from src.domain.exceptions.meal_suggestion_exceptions import (
    MealSuggestionSessionStoreUnavailableError,
)
from src.domain.model.meal_suggestion import (
    MealSuggestion,
    SuggestionSession,
)
from src.domain.model.translation_result import TranslationOutcome
from src.domain.services.meal_suggestion.nutrition_lookup_service import (
    IngredientMacros,
    MealMacros,
    NutritionLookupService,
)
from src.domain.services.meal_suggestion.parallel_recipe_generator import (
    ParallelRecipeGenerator,
)
from src.domain.services.meal_suggestion.recipe_attempt_builder import (
    PARALLEL_SINGLE_MEAL_TIMEOUT,
    SELECTED_RECIPE_ATTEMPT_TIMEOUT,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_meal_macros() -> MealMacros:
    """Minimal but realistic MealMacros for tests that exercise the nutrition path."""
    ingredient = IngredientMacros(
        name="eggs",
        quantity_g=100.0,
        calories=155.0,
        protein=13.0,
        carbs=1.1,
        fat=11.0,
        fiber=0.0,
        sugar=0.0,
        source_tier="T1_food_reference",
    )
    return MealMacros(
        calories=450.0,
        protein=40.0,
        carbs=30.0,
        fat=15.0,
        fiber=2.0,
        sugar=1.0,
        ingredients=[ingredient],
        t1_count=1,
        t2_count=0,
        t3_count=0,
    )


# Fixtures for mocked dependencies
@pytest.fixture
def mock_generation_service():
    """Mock MealGenerationServicePort."""
    return Mock()


@pytest.fixture
def mock_suggestion_repo():
    """Mock MealSuggestionRepositoryPort."""
    repo = AsyncMock()
    repo.get_session.return_value = None
    return repo


@pytest.fixture
def mock_user_repo():
    """Mock UserRepositoryPort."""
    profile_mock = Mock()
    profile_mock.gender = "female"
    profile_mock.age = 30
    profile_mock.height_cm = 165
    profile_mock.weight_kg = 60
    profile_mock.job_type = "desk"
    profile_mock.training_days_per_week = 4
    profile_mock.training_minutes_per_session = 60
    profile_mock.fitness_goal = "cut"
    profile_mock.body_fat_percentage = 25
    profile_mock.dietary_preferences = ["vegetarian"]
    profile_mock.allergies = ["peanuts"]
    profile_mock.meals_per_day = 3

    user_repo_mock = Mock()
    user_repo_mock.get_profile.return_value = profile_mock
    return user_repo_mock


@pytest.fixture
def recipe_generator(mock_generation_service):
    """Create ParallelRecipeGenerator with mocked dependencies."""
    from src.domain.services.meal_suggestion.macro_validation_service import (
        MacroValidationService,
    )
    from src.infra.services.ai.schemas import (
        DiscoveryMealsResponse,
        MealNamesResponse,
        RecipeDetailsResponse,
    )

    meal_macros = _make_meal_macros()
    nutrition_lookup = AsyncMock(spec=NutritionLookupService)
    nutrition_lookup.calculate_meal_macros = AsyncMock(return_value=meal_macros)
    nutrition_lookup.scale_to_target = Mock(return_value=meal_macros)
    return ParallelRecipeGenerator(
        generation_service=mock_generation_service,
        translation_service=None,  # No translation in tests
        macro_validator=MacroValidationService(),
        nutrition_lookup=nutrition_lookup,
        meal_names_schema_class=MealNamesResponse,
        discovery_meals_schema_class=DiscoveryMealsResponse,
        recipe_details_schema_class=RecipeDetailsResponse,
    )


# Fixtures for test data
@pytest.fixture
def mock_session():
    """Create a test session."""
    return SuggestionSession(
        id="test_session_123",
        user_id="user_456",
        meal_type="breakfast",
        meal_portion_type="standard",
        target_calories=500,
        ingredients=["eggs", "spinach"],
        cooking_time_minutes=15,
        language="en",
        dietary_preferences=["vegetarian"],
        allergies=["peanuts"],
    )


@pytest.fixture
def mock_recipe_response():
    """Create a valid RecipeDetailsResponse mock content."""
    return {
        "ingredients": [
            {"name": "Spinach", "amount": 1, "unit": "cup"},
            {"name": "Eggs", "amount": 2, "unit": "whole"},
        ],
        "recipe_steps": [
            {"step": 1, "instruction": "Wilt spinach", "duration_minutes": 5}
        ],
        "prep_time_minutes": 10,
    }


# Main Test Class
@pytest.mark.asyncio
class TestSuggestionGenerationPipeline:

    async def test_successful_generation_flow(
        self,
        recipe_generator,
        mock_generation_service,
        mock_session,
        mock_recipe_response,
    ):
        """
        Tests the ideal end-to-end flow:
        1. 4 unique names are generated.
        2. 4 recipe generations are started in parallel.
        3. At least 3 recipes are successfully generated.
        4. The service returns the 3 successful suggestions.
        """
        mock_names = {
            "meal_names": [
                "Spinach Omelette",
                "Tofu Scramble",
                "Green Smoothie",
                "Avocado Toast",
            ]
        }

        mock_generation_service.generate_meal_plan_async = AsyncMock(side_effect=[
            mock_names,
            mock_recipe_response,
            mock_recipe_response,
            mock_recipe_response,
            None,
            None,
        ])

        suggestions = await recipe_generator.generate(
            session=mock_session, exclude_meal_names=[]
        )

        assert len(suggestions) == 3
        assert all(isinstance(s, MealSuggestion) for s in suggestions)

        call_count = mock_generation_service.generate_meal_plan_async.call_count
        assert 5 <= call_count <= 6, f"Expected 5-6 calls, got {call_count}"

    async def test_failure_if_not_enough_names_generated(
        self, recipe_generator, mock_generation_service, mock_session
    ):
        """
        Tests that the process fails if Phase 1 does not return enough unique meal names.
        """
        mock_names = {"meal_names": ["Spinach Omelette", "Tofu Scramble"]}
        mock_generation_service.generate_meal_plan_async = AsyncMock(return_value=mock_names)

        with pytest.raises(
            RuntimeError, match="Could not generate enough unique meal names"
        ):
            await recipe_generator.generate(session=mock_session, exclude_meal_names=[])

    async def test_failure_if_not_enough_recipes_generated(
        self, recipe_generator, mock_generation_service, mock_session
    ):
        """
        Tests that the process fails if Phase 2 does not generate the minimum number of recipes.
        """
        mock_names = {
            "meal_names": [
                "Spinach Omelette",
                "Tofu Scramble",
                "Green Smoothie",
                "Avocado Toast",
            ]
        }

        mock_generation_service.generate_meal_plan_async = AsyncMock(side_effect=[
            mock_names,
            None,
            None,
            {"ingredients": [], "recipe_steps": [], "prep_time_minutes": 0},
            None,
        ])

        with pytest.raises(RuntimeError, match="Failed to generate any recipes"):
            await recipe_generator.generate(session=mock_session, exclude_meal_names=[])

    async def test_english_only_generation_prompts(
        self,
        recipe_generator,
        mock_generation_service,
        mock_session,
        mock_recipe_response,
    ):
        """
        Tests that generation prompts enforce English-only output regardless of session language.
        Translation to the target language happens in Phase 3 (post-generation), not during
        generation — this keeps prompts stable and avoids LLM mixing languages mid-response.
        """
        mock_session.language = "vi"

        mock_names = {
            "meal_names": [
                "Oatmeal Porridge",
                "Tofu Scramble",
                "Green Smoothie",
                "Avocado Toast",
            ]
        }
        mock_generation_service.generate_meal_plan_async = AsyncMock(side_effect=[
            mock_names,
            mock_recipe_response,
            mock_recipe_response,
            mock_recipe_response,
            mock_recipe_response,
        ])

        await recipe_generator.generate(session=mock_session, exclude_meal_names=[])

        all_calls = mock_generation_service.generate_meal_plan_async.call_args_list

        # Name generation: English-only enforcement
        name_gen_system_prompt = all_calls[0].args[1]
        assert "Output meal names in ENGLISH" in name_gen_system_prompt
        assert "JSON keys in English" in name_gen_system_prompt

        # Recipe generation: English-only enforcement for all text
        recipe_gen_system_prompt = all_calls[1].args[1]
        assert "MUST be in ENGLISH ONLY" in recipe_gen_system_prompt
        assert "JSON keys in English only" in recipe_gen_system_prompt

    def test_constants_are_set_correctly(self, recipe_generator):
        """Ensures performance-related constants are set to their expected optimized values."""
        assert recipe_generator.DEFAULT_SUGGESTIONS_COUNT == 3
        assert (
            recipe_generator.MIN_ACCEPTABLE_RESULTS == 1
        )  # relaxed to allow partial results
        assert PARALLEL_SINGLE_MEAL_TIMEOUT == 20  # reduced from 35 for faster timeouts
        assert SELECTED_RECIPE_ATTEMPT_TIMEOUT == 40


# -----------------------------------------------------------------------------
# Regression guards for the "strictly 1 serving + skip dietary_preferences" fix.
# See PR: fix(suggestions): strictly 1 serving & skip user dietary preferences.
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
class TestSessionCreationInvariants:
    """Verify `_create_new_session` enforces the post-fix invariants.

    Locks in two behaviors a future refactor might silently revert:
      1. `dietary_preferences` is ALWAYS empty on new sessions — even when
         the profile has preferences. Onboarding prefs were over-filtering
         AI output, so we skip them while still applying `allergies`.
      2. `allergies` still flow through from the profile — stripping diet
         prefs must NEVER weaken allergen avoidance.
    """

    @pytest.fixture
    def orchestration_service(
        self, mock_generation_service, mock_suggestion_repo, mock_user_repo
    ):
        from src.domain.services.meal_suggestion.suggestion_orchestration_service import (
            SuggestionOrchestrationService,
        )

        # Stub TDEE + portion services so _create_new_session doesn't depend
        # on real TDEE math. The test only cares about field passthrough.
        tdee_stub = Mock()
        tdee_stub.calculate_tdee = Mock(return_value=2000)
        portion_stub = Mock()
        portion_stub.get_target_for_meal_type = Mock(
            return_value=Mock(target_calories=600)
        )
        nutrition_lookup = AsyncMock(spec=NutritionLookupService)
        nutrition_lookup.calculate_meal_macros = AsyncMock(return_value=None)
        nutrition_lookup.scale_to_target = Mock(return_value=None)

        from src.infra.services.ai.schemas import (
            DiscoveryMealsResponse,
            MealNamesResponse,
            RecipeDetailsResponse,
        )

        service = SuggestionOrchestrationService(
            generation_service=mock_generation_service,
            suggestion_repo=mock_suggestion_repo,
            nutrition_lookup=nutrition_lookup,
            meal_names_schema_class=MealNamesResponse,
            discovery_meals_schema_class=DiscoveryMealsResponse,
            recipe_details_schema_class=RecipeDetailsResponse,
            tdee_service=tdee_stub,
            portion_service=portion_stub,
            profile_provider=lambda uid: mock_user_repo.get_profile(uid),
        )
        return service

    async def test_new_session_strips_profile_dietary_preferences(
        self, orchestration_service, mock_user_repo, monkeypatch
    ):
        """Profile has dietary_preferences=['vegetarian'] → session must have []."""
        # Stub the adjusted-daily helper so we don't need a UoW or DB.
        from src.domain.services.meal_suggestion import (
            suggestion_orchestration_service as mod,
        )

        async def _fake_adjusted(*args, **kwargs):
            return 2000

        monkeypatch.setattr(mod, "get_adjusted_daily_target", _fake_adjusted)

        # Profile fixture already sets dietary_preferences=["vegetarian"].
        assert mock_user_repo.get_profile("user_456").dietary_preferences == [
            "vegetarian"
        ]

        session, _ = await orchestration_service._create_new_session(
            user_id="user_456",
            meal_type="lunch",
            meal_portion_type="main",
            ingredients=["chicken"],
            cooking_time_minutes=20,
            language="en",
            servings=1,
        )

        assert (
            session.dietary_preferences == []
        ), "Session must strip profile dietary_preferences to avoid over-filtering"

    async def test_new_session_preserves_profile_allergies(
        self, orchestration_service, mock_user_repo, monkeypatch
    ):
        """Allergies must flow through unchanged — skipping diet prefs must
        NEVER weaken allergen avoidance (food safety)."""
        from src.domain.services.meal_suggestion import (
            suggestion_orchestration_service as mod,
        )

        async def _fake_adjusted(*args, **kwargs):
            return 2000

        monkeypatch.setattr(mod, "get_adjusted_daily_target", _fake_adjusted)

        session, _ = await orchestration_service._create_new_session(
            user_id="user_456",
            meal_type="lunch",
            meal_portion_type="main",
            ingredients=["chicken"],
            cooking_time_minutes=20,
            language="en",
            servings=1,
        )

        assert session.allergies == [
            "peanuts"
        ], "Profile allergies must still be applied — food safety critical"

    async def test_new_session_passes_servings_through(
        self, orchestration_service, monkeypatch
    ):
        """_create_new_session receives `servings` from the caller.
        Route handler hardcodes 1; this test locks the passthrough so the
        route-level coercion is the single source of truth (no silent
        default re-inflation inside the service)."""
        from src.domain.services.meal_suggestion import (
            suggestion_orchestration_service as mod,
        )

        async def _fake_adjusted(*args, **kwargs):
            return 2000

        monkeypatch.setattr(mod, "get_adjusted_daily_target", _fake_adjusted)

        session, _ = await orchestration_service._create_new_session(
            user_id="user_456",
            meal_type="lunch",
            meal_portion_type="main",
            ingredients=["chicken"],
            cooking_time_minutes=20,
            language="en",
            servings=1,
        )

        assert session.servings == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [TranslationOutcome.PARTIAL, TranslationOutcome.UNAVAILABLE],
)
async def test_persist_generation_result_keeps_fallback_suggestions_for_non_english(
    outcome,
):
    """Translation fallback suggestions must remain selectable from the session."""
    from src.domain.services.meal_suggestion.suggestion_orchestration_service import (
        SuggestionOrchestrationService,
    )

    repo = AsyncMock()
    service = object.__new__(SuggestionOrchestrationService)
    service._repo = repo

    session = Mock()
    session.language = "vi"
    suggestion = Mock()
    suggestion.id = "suggestion-1"
    suggestion.meal_name = "Fallback meal"
    suggestion.translation_outcome = outcome
    suggestions = [suggestion]

    await service._persist_generation_result(
        session=session,
        suggestions=suggestions,
        existing_session=False,
    )

    repo.save_suggestions.assert_awaited_once_with(suggestions)


@pytest.mark.asyncio
async def test_generate_discovery_appends_existing_discovery_meals_on_load_more(
    mock_generation_service,
):
    """Load-more must preserve earlier discovery ids for later /recipes selection."""
    from src.domain.services.meal_suggestion.suggestion_orchestration_service import (
        SuggestionOrchestrationService,
    )
    from src.infra.services.ai.schemas import (
        DiscoveryMealsResponse,
        MealNamesResponse,
        RecipeDetailsResponse,
    )

    existing_session = SuggestionSession(
        id="sess-discovery",
        user_id="user-1",
        meal_type="lunch",
        meal_portion_type="main",
        target_calories=400,
        ingredients=["carp"],
        discovery_meals=[
            {
                "id": "disc_old",
                "name": "Ginger Carp Steamed",
                "english_name": "Ginger Carp Steamed",
                "calories": 300,
                "protein": 30,
                "carbs": 20,
                "fat": 8,
            }
        ],
        shown_meal_names=["Ginger Carp Steamed"],
    )
    repo = AsyncMock()
    repo.get_session.return_value = existing_session

    nutrition_lookup = AsyncMock(spec=NutritionLookupService)
    service = SuggestionOrchestrationService(
        generation_service=mock_generation_service,
        suggestion_repo=repo,
        nutrition_lookup=nutrition_lookup,
        meal_names_schema_class=MealNamesResponse,
        discovery_meals_schema_class=DiscoveryMealsResponse,
        recipe_details_schema_class=RecipeDetailsResponse,
        profile_provider=lambda user_id: Mock(),
    )
    service._recipe_generator.generate_discovery = AsyncMock(
        return_value=[
            {
                "id": "disc_new",
                "name": "Lemongrass Carp Grilled",
                "english_name": "Lemongrass Carp Grilled",
                "calories": 350,
                "protein": 35,
                "carbs": 18,
                "fat": 10,
            }
        ]
    )

    session, meals = await service.generate_discovery(
        user_id="user-1",
        meal_type="lunch",
        meal_portion_type="main",
        ingredients=["carp"],
        session_id="sess-discovery",
        count=10,
    )

    assert [meal["id"] for meal in meals] == ["disc_new"]
    assert [meal["id"] for meal in session.discovery_meals] == [
        "disc_old",
        "disc_new",
    ]
    repo.update_session.assert_awaited_once_with(session)


@pytest.mark.asyncio
async def test_generate_discovery_accepts_async_profile_provider(mock_generation_service):
    from src.domain.services.meal_suggestion.suggestion_orchestration_service import (
        SuggestionOrchestrationService,
    )
    from src.infra.services.ai.schemas import (
        DiscoveryMealsResponse,
        MealNamesResponse,
        RecipeDetailsResponse,
    )

    repo = AsyncMock()
    nutrition_lookup = AsyncMock(spec=NutritionLookupService)
    profile_provider = AsyncMock(return_value=Mock(meals_per_day=3, allergies=[]))
    service = SuggestionOrchestrationService(
        generation_service=mock_generation_service,
        suggestion_repo=repo,
        nutrition_lookup=nutrition_lookup,
        meal_names_schema_class=MealNamesResponse,
        discovery_meals_schema_class=DiscoveryMealsResponse,
        recipe_details_schema_class=RecipeDetailsResponse,
        profile_provider=profile_provider,
    )
    service._recipe_generator.generate_discovery = AsyncMock(
        return_value=[
            {
                "id": "disc_async",
                "name": "Async Bowl",
                "english_name": "Async Bowl",
                "calories": 400,
                "protein": 30,
                "carbs": 40,
                "fat": 12,
            }
        ]
    )

    session, meals = await service.generate_discovery(
        user_id="user-1",
        meal_type="lunch",
        meal_portion_type="main",
        ingredients=["tofu"],
        count=1,
    )

    profile_provider.assert_awaited_once_with("user-1")
    repo.save_session.assert_awaited_once_with(session)
    assert meals[0]["id"] == "disc_async"


@pytest.mark.asyncio
async def test_generate_discovery_returns_meals_when_session_store_write_fails(
    mock_generation_service,
    monkeypatch,
):
    """Discovery can degrade because /recipes accepts client-selected meal objects."""
    from src.domain.services.meal_suggestion import (
        suggestion_orchestration_service as mod,
    )
    from src.domain.services.meal_suggestion.suggestion_orchestration_service import (
        SuggestionOrchestrationService,
    )
    from src.infra.services.ai.schemas import (
        DiscoveryMealsResponse,
        MealNamesResponse,
        RecipeDetailsResponse,
    )

    async def _fake_adjusted(*args, **kwargs):
        return 2000

    monkeypatch.setattr(mod, "get_adjusted_daily_target", _fake_adjusted)

    profile = Mock()
    profile.meals_per_day = 3
    profile.allergies = []
    repo = AsyncMock()
    repo.save_session.side_effect = MealSuggestionSessionStoreUnavailableError()
    nutrition_lookup = AsyncMock(spec=NutritionLookupService)
    service = SuggestionOrchestrationService(
        generation_service=mock_generation_service,
        suggestion_repo=repo,
        nutrition_lookup=nutrition_lookup,
        meal_names_schema_class=MealNamesResponse,
        discovery_meals_schema_class=DiscoveryMealsResponse,
        recipe_details_schema_class=RecipeDetailsResponse,
        profile_provider=lambda user_id: profile,
    )
    generated_meals = [
        {
            "id": "disc_1",
            "name": "Ginger Carp Steamed",
            "english_name": "Ginger Carp Steamed",
            "calories": 300,
            "protein": 30,
            "carbs": 20,
            "fat": 8,
        }
    ]
    service._recipe_generator.generate_discovery = AsyncMock(
        return_value=generated_meals
    )

    session, meals = await service.generate_discovery(
        user_id="user-1",
        meal_type="lunch",
        meal_portion_type="main",
        ingredients=["carp"],
        count=10,
    )

    assert meals == generated_meals
    assert session.discovery_meals == generated_meals
    assert session.shown_meal_names == ["Ginger Carp Steamed"]
    repo.save_session.assert_awaited_once_with(session)
