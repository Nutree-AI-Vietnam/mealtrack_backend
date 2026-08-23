"""
Handler for editing meal ingredients.
"""

import logging
from dataclasses import replace
from typing import Any

from src.api.exceptions import (
    AuthorizationException,
    ConflictException,
    ResourceNotFoundException,
    ValidationException,
)
from src.app.commands.meal import EditMealCommand
from src.app.commands.meal.create_manual_meal_command import ManualMealItem
from src.app.events.base import EventHandler, handles
from src.app.events.meal import MealEditedEvent
from src.app.services.cache_invalidation_service import CacheInvalidationService
from src.app.services.manual_meal_nutrition_resolver import (
    ManualMealNutritionResolver,
)
from src.domain.model.meal import FoodItemTranslation, MealStatus
from src.domain.model.meal_projection import MealProjection
from src.domain.model.nutrition import Macros, NutritionOverride
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.provider_budget_port import ProviderBudgetPort
from src.domain.services.meal_type_determination_service import (
    determine_meal_type_from_timestamp,
)
from src.domain.services.nutrition_calculation_service import (
    authoritative_units_match,
    canonicalize_authoritative_quantity,
)
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)


@handles(EditMealCommand)
class EditMealCommandHandler(EventHandler[EditMealCommand, dict[str, Any]]):
    """Handler for editing meal ingredients."""

    def __init__(
        self,
        uow: AsyncUnitOfWorkPort,
        cache_invalidation: CacheInvalidationService | None = None,
        nutrition_resolver: ManualMealNutritionResolver | None = None,
        provider=None,
        provider_budget: ProviderBudgetPort | None = None,
        provider_rpm: int | None = None,
        uow_factory=None,
    ):
        self.uow = uow
        self.uow_factory = uow_factory
        self.cache_invalidation = cache_invalidation
        self.nutrition_resolver = nutrition_resolver or ManualMealNutritionResolver(
            provider=provider,
            provider_budget=provider_budget,
            provider_rpm=provider_rpm,
            uow_factory=uow_factory,
        )

    async def handle(self, command: EditMealCommand) -> dict[str, Any]:
        """Handle meal editing operations."""
        if command.nutrition_contract_version == 2 and self.uow_factory is not None:
            return await self._handle_v2(command)
        async with self.uow as uow:
            try:
                # 1. Validate meal exists
                meal = await uow.meals.find_by_id(
                    command.meal_id, projection=MealProjection.FULL_WITH_TRANSLATIONS
                )
                if not meal:
                    raise ResourceNotFoundException("Meal not found")

                # 1a. Check ownership if user_id provided
                if command.user_id and meal.user_id != command.user_id:
                    raise AuthorizationException(
                        "You do not have permission to modify this meal"
                    )

                if meal.status != MealStatus.READY:
                    raise ValidationException("Meal must be in READY status to edit")
                reservation = await self._reserve_v2_write(command, uow)
                if reservation and reservation.state == "replay":
                    if reservation.target_meal_id != command.meal_id:
                        raise ValidationException(
                            "Idempotent edit result does not match this meal",
                            error_code="IDEMPOTENCY_RESULT_MISMATCH",
                        )
                    return reservation.response or {
                        "success": True,
                        "meal_id": command.meal_id,
                    }

                # 2. Apply food item changes
                food_item_changes = command.food_item_changes
                if command.nutrition_contract_version == 2:
                    food_item_changes = await self._prepare_v2_changes(
                        meal.nutrition.food_items if meal.nutrition else [],
                        food_item_changes,
                        uow,
                    )
                    meal = await self._reload_v2_meal_for_commit(meal, command, uow)
                updated_food_items = await self._apply_food_item_changes(
                    meal.nutrition.food_items if meal.nutrition else [],
                    food_item_changes,
                    food_reference_repository=getattr(uow, "food_references", None),
                )
                self._realign_translations_after_food_item_changes(
                    meal, updated_food_items
                )

                # 3. Recalculate nutrition from current ingredients.
                updated_nutrition = self._calculate_total_nutrition(updated_food_items)
                existing_override = (
                    meal.nutrition.nutrition_override if meal.nutrition else None
                )
                # Meal-level override is only for intentional whole-meal edits.
                # Ingredient composition changes must clear it so totals follow
                # the ingredient sum (unless this request sets a new override).
                if command.nutrition_override is not None:
                    nutrition_override = NutritionOverride(
                        calories=command.nutrition_override.calories,
                        protein=command.nutrition_override.protein,
                        carbs=command.nutrition_override.carbs,
                        fat=command.nutrition_override.fat,
                    )
                    updated_nutrition.nutrition_override = nutrition_override
                    updated_nutrition.macros = Macros(
                        protein=nutrition_override.protein,
                        carbs=nutrition_override.carbs,
                        fat=nutrition_override.fat,
                        fiber=updated_nutrition.macros.fiber,
                        sugar=updated_nutrition.macros.sugar,
                    )
                elif command.food_item_changes:
                    updated_nutrition.nutrition_override = None
                elif existing_override is not None:
                    nutrition_override = NutritionOverride(
                        calories=existing_override.calories,
                        protein=existing_override.protein,
                        carbs=existing_override.carbs,
                        fat=existing_override.fat,
                    )
                    updated_nutrition.nutrition_override = nutrition_override
                    updated_nutrition.macros = Macros(
                        protein=nutrition_override.protein,
                        carbs=nutrition_override.carbs,
                        fat=nutrition_override.fat,
                        fiber=updated_nutrition.macros.fiber,
                        sugar=updated_nutrition.macros.sugar,
                    )

                updated_created_at = command.created_at or meal.created_at
                if command.meal_type is not None:
                    updated_meal_type = command.meal_type
                elif command.created_at is not None:
                    updated_meal_type = determine_meal_type_from_timestamp(
                        command.created_at
                    )
                else:
                    updated_meal_type = meal.meal_type

                # 4. Update meal
                updated_meal = meal.mark_edited(
                    nutrition=updated_nutrition,
                    dish_name=(
                        command.dish_name
                        if command.dish_name is not None
                        else meal.dish_name
                    ),
                    created_at=updated_created_at,
                    meal_type=updated_meal_type,
                )

                # 5. Persist changes
                saved_meal = await uow.meals.save(updated_meal)
                await self._save_realigned_translations(uow, updated_meal.translations)
                changes_summary = self._generate_changes_summary(food_item_changes)
                if reservation:
                    await uow.meal_write_operations.complete(
                        reservation,
                        target_meal_id=saved_meal.meal_id,
                        response={
                            "success": True,
                            "meal_id": saved_meal.meal_id,
                            "message": f"Meal updated successfully. {changes_summary}",
                            "dish_name": saved_meal.dish_name or "Meal",
                            "total_calories": updated_nutrition.calories,
                            "updated_nutrition": {
                                "calories": updated_nutrition.calories,
                                "protein": updated_nutrition.macros.protein,
                                "carbs": updated_nutrition.macros.carbs,
                                "fat": updated_nutrition.macros.fat,
                            },
                            "updated_food_items": [
                                item.to_dict() for item in updated_food_items
                            ],
                            "edit_metadata": {
                                "edit_count": saved_meal.edit_count,
                                "changes_summary": changes_summary,
                            },
                        },
                    )

                old_meal_date = (meal.created_at or utc_now()).date()
                meal_date = (saved_meal.created_at or utc_now()).date()
                if self.cache_invalidation:
                    if old_meal_date != meal_date:
                        await self.cache_invalidation.enqueue_meal_invalidation(
                            uow.outbox,
                            saved_meal.user_id,
                            old_meal_date,
                        )
                    await self.cache_invalidation.enqueue_meal_invalidation(
                        uow.outbox,
                        saved_meal.user_id,
                        meal_date,
                    )
                await uow.commit()

                # 6. Calculate nutrition delta for event
                nutrition_delta = self._calculate_nutrition_delta(
                    meal.nutrition, updated_nutrition
                )

                response = {
                    "success": True,
                    "meal_id": saved_meal.meal_id,
                    "message": f"Meal updated successfully. {changes_summary}",
                    "dish_name": saved_meal.dish_name or "Meal",
                    "total_calories": updated_nutrition.calories,
                    "updated_nutrition": {
                        "calories": updated_nutrition.calories,
                        "protein": updated_nutrition.macros.protein,
                        "carbs": updated_nutrition.macros.carbs,
                        "fat": updated_nutrition.macros.fat,
                    },
                    "updated_food_items": [
                        item.to_dict() for item in updated_food_items
                    ],
                    "edit_metadata": {
                        "edit_count": saved_meal.edit_count,
                        "changes_summary": changes_summary,
                    },
                    "events": [
                        MealEditedEvent(
                            aggregate_id=saved_meal.meal_id,
                            meal_id=saved_meal.meal_id,
                            user_id=saved_meal.user_id,
                            edit_type="ingredients_updated",
                            changes_summary=changes_summary,
                            nutrition_delta=nutrition_delta,
                            edit_count=saved_meal.edit_count,
                        )
                    ],
                }
            except ValueError as e:
                await uow.rollback()
                logger.warning(f"Validation error editing meal: {str(e)}")
                raise ValidationException(str(e)) from None
            except Exception:
                await uow.rollback()
                raise

        return response

    async def _handle_v2(self, command: EditMealCommand) -> dict[str, Any]:
        """Keep lease reservation and provider/reference resolution out of the write UoW."""
        preflight_meal = await self._preflight_v2_meal(command)
        reservation = await self._reserve_v2_write_short(command)
        if reservation.state == "replay":
            if reservation.target_meal_id != command.meal_id:
                raise ValidationException(
                    "Idempotent edit result does not match this meal",
                    error_code="IDEMPOTENCY_RESULT_MISMATCH",
                )
            return reservation.response or {
                "success": True,
                "meal_id": command.meal_id,
            }

        try:
            resolved_items: list[ManualMealItem] = []
            async with self.uow_factory() as resolve_uow:
                prepared_changes = await self._prepare_v2_changes(
                    (preflight_meal.nutrition.food_items or [])
                    if preflight_meal.nutrition
                    else [],
                    command.food_item_changes,
                    resolve_uow,
                    revalidate_local=False,
                    resolved_items_out=resolved_items,
                )

            async with self.uow as uow:
                meal = await self._reload_v2_meal_for_commit(
                    preflight_meal, command, uow
                )
                await self.nutrition_resolver.revalidate_local_items(
                    resolved_items, uow.food_references
                )
                updated_food_items = await self._apply_food_item_changes(
                    (meal.nutrition.food_items or []) if meal.nutrition else [],
                    prepared_changes,
                    food_reference_repository=getattr(uow, "food_references", None),
                )
                self._realign_translations_after_food_item_changes(
                    meal, updated_food_items
                )
                updated_nutrition = self._calculate_total_nutrition(updated_food_items)
                existing_override = (
                    meal.nutrition.nutrition_override if meal.nutrition else None
                )
                if command.nutrition_override is not None:
                    nutrition_override = NutritionOverride(
                        calories=command.nutrition_override.calories,
                        protein=command.nutrition_override.protein,
                        carbs=command.nutrition_override.carbs,
                        fat=command.nutrition_override.fat,
                    )
                    updated_nutrition.nutrition_override = nutrition_override
                    updated_nutrition.macros = Macros(
                        protein=nutrition_override.protein,
                        carbs=nutrition_override.carbs,
                        fat=nutrition_override.fat,
                        fiber=updated_nutrition.macros.fiber,
                        sugar=updated_nutrition.macros.sugar,
                    )
                elif command.food_item_changes:
                    updated_nutrition.nutrition_override = None
                elif existing_override is not None:
                    nutrition_override = NutritionOverride(
                        calories=existing_override.calories,
                        protein=existing_override.protein,
                        carbs=existing_override.carbs,
                        fat=existing_override.fat,
                    )
                    updated_nutrition.nutrition_override = nutrition_override
                    updated_nutrition.macros = Macros(
                        protein=nutrition_override.protein,
                        carbs=nutrition_override.carbs,
                        fat=nutrition_override.fat,
                        fiber=updated_nutrition.macros.fiber,
                        sugar=updated_nutrition.macros.sugar,
                    )

                updated_meal = meal.mark_edited(
                    nutrition=updated_nutrition,
                    dish_name=(
                        command.dish_name
                        if command.dish_name is not None
                        else meal.dish_name
                    ),
                    created_at=command.created_at or meal.created_at,
                    meal_type=(
                        command.meal_type
                        if command.meal_type is not None
                        else (
                            determine_meal_type_from_timestamp(command.created_at)
                            if command.created_at is not None
                            else meal.meal_type
                        )
                    ),
                )
                saved_meal = await uow.meals.save(updated_meal)
                await self._save_realigned_translations(uow, updated_meal.translations)
                changes_summary = self._generate_changes_summary(prepared_changes)
                replay_response = {
                    "success": True,
                    "meal_id": saved_meal.meal_id,
                    "message": f"Meal updated successfully. {changes_summary}",
                    "dish_name": saved_meal.dish_name or "Meal",
                    "total_calories": updated_nutrition.calories,
                    "updated_nutrition": {
                        "calories": updated_nutrition.calories,
                        "protein": updated_nutrition.macros.protein,
                        "carbs": updated_nutrition.macros.carbs,
                        "fat": updated_nutrition.macros.fat,
                    },
                    "updated_food_items": [
                        item.to_dict() for item in updated_food_items
                    ],
                    "edit_metadata": {
                        "edit_count": saved_meal.edit_count,
                        "changes_summary": changes_summary,
                    },
                }
                await uow.meal_write_operations.complete(
                    reservation,
                    target_meal_id=saved_meal.meal_id,
                    response=replay_response,
                )
                old_meal_date = (meal.created_at or utc_now()).date()
                meal_date = (saved_meal.created_at or utc_now()).date()
                if self.cache_invalidation:
                    if old_meal_date != meal_date:
                        await self.cache_invalidation.enqueue_meal_invalidation(
                            uow.outbox,
                            saved_meal.user_id,
                            old_meal_date,
                        )
                    await self.cache_invalidation.enqueue_meal_invalidation(
                        uow.outbox,
                        saved_meal.user_id,
                        meal_date,
                    )
                await uow.commit()

            replay_response["events"] = [
                MealEditedEvent(
                    aggregate_id=saved_meal.meal_id,
                    meal_id=saved_meal.meal_id,
                    user_id=saved_meal.user_id,
                    edit_type="ingredients_updated",
                    changes_summary=changes_summary,
                    nutrition_delta=self._calculate_nutrition_delta(
                        meal.nutrition, updated_nutrition
                    ),
                    edit_count=saved_meal.edit_count,
                )
            ]
            return replay_response
        except ValueError as exc:
            await self._release_v2_write(reservation)
            logger.warning("Validation error editing meal: %s", str(exc))
            raise ValidationException(str(exc)) from None
        except Exception:
            await self._release_v2_write(reservation)
            raise

    async def _preflight_v2_meal(self, command):
        async with self.uow_factory() as uow:
            meal = await uow.meals.find_by_id(
                command.meal_id, projection=MealProjection.FULL_WITH_TRANSLATIONS
            )
        if not meal:
            raise ResourceNotFoundException("Meal not found")
        if command.user_id and meal.user_id != command.user_id:
            raise AuthorizationException(
                "You do not have permission to modify this meal"
            )
        if meal.status != MealStatus.READY:
            raise ValidationException("Meal must be in READY status to edit")
        return meal

    async def _reserve_v2_write_short(self, command):
        async with self.uow_factory() as uow:
            return await self._reserve_v2_write(command, uow)

    async def _release_v2_write(self, reservation):
        async with self.uow_factory() as uow:
            await uow.meal_write_operations.release(reservation)

    async def _reserve_v2_write(self, command, uow):
        if command.nutrition_contract_version != 2:
            return None
        if not command.idempotency_key or not command.request_fingerprint:
            raise ValidationException(
                "v2 meal edits require idempotency metadata",
                error_code="IDEMPOTENCY_KEY_REQUIRED",
            )
        reservation = await uow.meal_write_operations.reserve(
            user_id=command.user_id,
            operation="edit_meal",
            idempotency_key=command.idempotency_key,
            request_fingerprint=command.request_fingerprint,
        )
        if reservation.state == "fingerprint_conflict":
            raise ConflictException(
                "Idempotency-Key was already used for a different request",
                error_code="IDEMPOTENCY_KEY_REUSED",
            )
        if reservation.state == "in_progress":
            raise ConflictException(
                "The same meal edit is already in progress",
                error_code="IDEMPOTENCY_IN_PROGRESS",
            )
        return reservation

    async def _prepare_v2_changes(
        self,
        current_food_items,
        changes,
        uow,
        *,
        revalidate_local=True,
        resolved_items_out=None,
    ):
        """Resolve source changes from backend references before strategies run."""
        current_by_id = {item.id: item for item in current_food_items}
        prepared = []
        resolved_items = []
        for change in changes:
            if change.action == "remove":
                if change.id not in current_by_id:
                    raise ValueError("v2 remove requires an owned food item id")
                prepared.append(change)
                continue

            if change.action == "update" and (
                not change.id or change.id not in current_by_id
            ):
                raise ValueError("v2 update requires an owned food item id")

            if change.action == "add" and change.origin is None:
                raise ValueError("v2 add requires origin")

            if change.origin is not None:
                existing = current_by_id.get(change.id)
                item = ManualMealItem(
                    fdc_id=change.fdc_id,
                    name=change.name or (existing.name if existing else None),
                    quantity=change.quantity
                    or (existing.quantity if existing else 100),
                    unit=change.unit or (existing.unit if existing else "g"),
                    custom_nutrition=(
                        self._to_manual_custom_nutrition(change.custom_nutrition)
                        if change.custom_nutrition
                        else None
                    ),
                    allowed_units=change.allowed_units,
                    origin=change.origin,
                    food_reference_id=change.food_reference_id,
                    source_namespace=change.source_namespace,
                    source_food_id=change.source_food_id,
                )
                resolved = await self.nutrition_resolver.resolve_items(
                    [item],
                    uow.food_references,
                    contract_version=2,
                )
                authoritative = resolved[0]
                resolved_items.append(authoritative)
                prepared.append(
                    replace(
                        change,
                        fdc_id=authoritative.fdc_id,
                        name=authoritative.name,
                        quantity=authoritative.quantity,
                        unit=authoritative.unit,
                        custom_nutrition=self._to_domain_custom_nutrition(
                            authoritative.custom_nutrition
                        ),
                        allowed_units=authoritative.allowed_units,
                        food_reference_id=authoritative.food_reference_id,
                        source_namespace=authoritative.source_namespace,
                        source_food_id=authoritative.source_food_id,
                        source_snapshot=authoritative.source_snapshot,
                    )
                )
                continue

            if change.nutrition_override is not None or change.clear_nutrition_override:
                if change.action == "update" and not change.id:
                    raise ValueError("nutrition override requires an owned item update")
                prepared.append(change)
                continue

            existing = current_by_id[change.id]
            prepared.append(self._canonicalize_snapshot_unit(existing, change))
        if revalidate_local:
            await self.nutrition_resolver.revalidate_local_items(
                resolved_items, uow.food_references
            )
        if resolved_items_out is not None:
            resolved_items_out.extend(resolved_items)
        return prepared

    async def _reload_v2_meal_for_commit(self, loaded_meal, command, uow):
        loader = getattr(uow.meals, "find_by_id_for_update", None)
        if loader is None:
            return loaded_meal
        locked_meal = await loader(
            command.meal_id,
            projection=MealProjection.FULL_WITH_TRANSLATIONS,
        )
        if locked_meal is None:
            raise ResourceNotFoundException("Meal not found")
        if command.user_id and locked_meal.user_id != command.user_id:
            raise AuthorizationException(
                "You do not have permission to modify this meal"
            )
        if locked_meal.status != MealStatus.READY:
            raise ValidationException("Meal must be in READY status to edit")
        if (
            locked_meal.edit_count != loaded_meal.edit_count
            or locked_meal.updated_at != loaded_meal.updated_at
        ):
            raise ValidationException(
                "Meal changed while the authoritative nutrition was resolving",
                error_code="MEAL_WRITE_CONFLICT",
            )
        return locked_meal

    @staticmethod
    def _canonicalize_snapshot_unit(existing_item, change):
        unit = change.unit
        if unit is None:
            return change
        existing_unit = existing_item.unit or "g"
        if authoritative_units_match(unit, existing_unit):
            if unit.strip() != existing_unit:
                return replace(change, unit=existing_unit)
            return change
        snapshot = existing_item.source_snapshot or {}
        allowed_units = (
            snapshot.get("allowed_units") or existing_item.allowed_units or []
        )
        if not allowed_units:
            raise ValueError("v2 quantity updates require an immutable source snapshot")
        quantity = (
            change.quantity if change.quantity is not None else existing_item.quantity
        )
        canonical_quantity, canonical_unit, used_fallback = (
            canonicalize_authoritative_quantity(
                quantity,
                unit,
                allowed_units,
                existing_item.name,
            )
        )
        if used_fallback:
            return replace(
                change,
                quantity=canonical_quantity,
                unit=canonical_unit,
            )
        return change

    @staticmethod
    def _to_manual_custom_nutrition(value):
        if value is None:
            return None
        from src.app.commands.meal.create_manual_meal_command import CustomNutrition

        return CustomNutrition(
            calories_per_100g=value.calories_per_100g,
            protein_per_100g=value.protein_per_100g,
            carbs_per_100g=value.carbs_per_100g,
            fat_per_100g=value.fat_per_100g,
            fiber_per_100g=value.fiber_per_100g,
            sugar_per_100g=value.sugar_per_100g,
        )

    @staticmethod
    def _to_domain_custom_nutrition(value):
        if value is None:
            return None
        from src.domain.model.meal.food_item_change import CustomNutritionData

        return CustomNutritionData(
            calories_per_100g=value.calories_per_100g,
            protein_per_100g=value.protein_per_100g,
            carbs_per_100g=value.carbs_per_100g,
            fat_per_100g=value.fat_per_100g,
            fiber_per_100g=value.fiber_per_100g,
            sugar_per_100g=value.sugar_per_100g,
        )

    async def _apply_food_item_changes(
        self,
        current_food_items,
        changes,
        food_reference_repository=None,
    ):
        """Apply food item changes to current list using strategy pattern."""
        from src.domain.services import NutritionCalculationService
        from src.domain.strategies.meal_edit_strategies import (
            FoodItemChangeStrategyFactory,
        )

        # Convert current items to dict for easier manipulation
        food_items_dict = {}
        if current_food_items:
            for item in current_food_items:
                food_items_dict[item.id] = item

        # Initialize nutrition service and create strategies
        nutrition_service = NutritionCalculationService()
        strategies = FoodItemChangeStrategyFactory.create_strategies(
            nutrition_service,
            food_reference_repository=food_reference_repository,
        )

        # Apply each change using the appropriate strategy
        for change in changes:
            strategy = strategies.get(change.action)
            if strategy:
                await strategy.apply(food_items_dict, change)
            else:
                logger.warning(f"Unknown action: {change.action}")

        return list(food_items_dict.values())

    def _realign_translations_after_food_item_changes(self, meal, updated_food_items):
        """Keep cached translations aligned to the edited food item order."""
        if not meal.translations or not meal.nutrition or not meal.nutrition.food_items:
            return

        previous_food_items = meal.nutrition.food_items
        for translation in meal.translations.values():
            translated_names_by_id = self._translated_names_by_id(
                translation, previous_food_items
            )
            if not translated_names_by_id:
                continue

            realigned_ingredients = []
            realigned_food_items = []
            missing_translation = False
            for item in updated_food_items:
                translated_name = translated_names_by_id.get(str(item.id))
                if not translated_name:
                    missing_translation = True
                    break
                realigned_ingredients.append(translated_name)
                realigned_food_items.append(
                    FoodItemTranslation(
                        food_item_id=str(item.id),
                        name=translated_name,
                    )
                )
            if missing_translation:
                continue

            translation.meal_ingredients = realigned_ingredients
            translation.food_items = realigned_food_items

    def _translated_names_by_id(self, translation, previous_food_items):
        translated_names_by_id = {
            str(item.food_item_id): item.name
            for item in translation.food_items
            if item.name
        }

        if translation.meal_ingredients and len(translation.meal_ingredients) == len(
            previous_food_items
        ):
            translated_names_by_id.update(
                {
                    str(item.id): translation.meal_ingredients[index]
                    for index, item in enumerate(previous_food_items)
                    if str(item.id) not in translated_names_by_id
                    and translation.meal_ingredients[index]
                }
            )

        return translated_names_by_id

    async def _save_realigned_translations(self, uow, translations):
        if not translations:
            return

        translation_repo = getattr(uow, "meal_translations", None)
        if translation_repo is None:
            return

        for translation in translations.values():
            await translation_repo.save(translation)

    def _calculate_total_nutrition(self, food_items):
        """Calculate total nutrition from food items using nutrition service."""
        from src.domain.services import NutritionCalculationService

        nutrition_service = NutritionCalculationService()
        return nutrition_service.calculate_meal_total(food_items)

    def _calculate_nutrition_delta(self, old_nutrition, new_nutrition):
        """Calculate the difference in nutrition values."""
        if not old_nutrition:
            return {
                "calories": new_nutrition.calories,
                "protein": new_nutrition.macros.protein,
                "carbs": new_nutrition.macros.carbs,
                "fat": new_nutrition.macros.fat,
            }

        return {
            "calories": new_nutrition.calories - old_nutrition.calories,
            "protein": new_nutrition.macros.protein - old_nutrition.macros.protein,
            "carbs": new_nutrition.macros.carbs - old_nutrition.macros.carbs,
            "fat": new_nutrition.macros.fat - old_nutrition.macros.fat,
        }

    def _generate_changes_summary(self, changes):
        """Generate a human-readable summary of changes."""
        summary_parts = []
        for change in changes:
            if change.action == "add":
                summary_parts.append(f"Added {change.name or 'ingredient'}")
            elif change.action == "remove":
                summary_parts.append("Removed ingredient")
            elif change.action == "update":
                summary_parts.append("Updated portion")

        return "; ".join(summary_parts) if summary_parts else "Updated meal"
