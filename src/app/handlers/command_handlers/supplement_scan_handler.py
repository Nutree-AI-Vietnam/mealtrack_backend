"""Read a supplement label photo, save the product, list it and log today's dose.

A protein powder, gainer or meal shake is saved as a meal from the same read,
with no second model call. A retry with the same Idempotency-Key replays the
stored reply; the meal takes the client's ``intake_id`` as its id, so a retry
after a failed reply finds that meal instead of saving a second one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from src.api.exceptions import (
    ConflictException,
    ExternalServiceException,
    ValidationException,
)
from src.app.commands.meal.create_manual_meal_command import (
    CreateManualMealCommand,
    CustomNutrition,
    ManualMealItem,
)
from src.app.commands.supplement import ScanSupplementLabelCommand
from src.app.events.base import EventHandler, handles
from src.app.handlers.command_handlers.supplement_command_handlers import (
    DayPurge,
    SupplementWrite,
)
from src.app.services.describe_log_replay import complete, run_once
from src.app.services.supplement_payloads import (
    intake_payload,
    list_entry_payload,
    product_payload,
)
from src.domain.exceptions.ai_exceptions import AIOutputValidationError
from src.domain.model.ai.supplement_label_contract import SupplementLabelResponse
from src.domain.model.supplement import SupplementProduct
from src.domain.model.supplement.label_scan import is_food, product_from_label
from src.domain.strategies.supplement_label_strategy import (
    SupplementLabelAnalysisStrategy,
)
from src.domain.utils.timezone_utils import (
    get_zone_info,
    resolve_user_timezone_async,
    utc_now,
)

# Longer than a scan can run: download, the model call and the write around it.
SCAN_LEASE_SECONDS = 120
# Reading stops well inside the lease, so a retry never takes over a live scan.
READ_TIMEOUT_SECONDS = 90
_DISH_NAME_LIMIT = 255


def _unreadable() -> ValidationException:
    return ValidationException(
        "The label could not be read; take the photo again",
        "SUPPLEMENT_LABEL_UNREADABLE",
    )


def _meal_summary(meal: Any) -> dict[str, Any]:
    """JSON-safe enough to store as the replay reply; the app reloads the day."""
    nutrition = getattr(meal, "nutrition", None)
    macros = getattr(nutrition, "macros", None)
    return {
        "meal_id": meal.meal_id,
        "dish_name": meal.dish_name,
        "calories": round(float(nutrition.calories), 1) if nutrition else None,
        "protein": getattr(macros, "protein", None),
        "carbs": getattr(macros, "carbs", None),
        "fat": getattr(macros, "fat", None),
    }


@handles(ScanSupplementLabelCommand)
class ScanSupplementLabelCommandHandler(
    SupplementWrite, EventHandler[ScanSupplementLabelCommand, dict]
):
    def __init__(
        self,
        uow_factory: Callable[[], Any],
        vision_service: Any,
        download_image_bytes: Callable[[str], Awaitable[bytes]],
        event_bus: Any,
        purge_day: DayPurge | None = None,
    ):
        super().__init__(uow_factory, purge_day)
        self.vision_service = vision_service
        self.download_image_bytes = download_image_bytes
        self.event_bus = event_bus

    async def handle(self, cmd: ScanSupplementLabelCommand) -> dict:
        return await run_once(
            self.uow_factory,
            cmd.user_id,
            cmd.replay_key,
            lease_seconds=SCAN_LEASE_SECONDS,
            log=lambda reservation: self._scan(cmd, reservation),
        )

    async def _scan(self, cmd: ScanSupplementLabelCommand, reservation: Any) -> dict:
        # A bad time fails before the model call spends anything.
        self._taken(cmd.taken_at, utc_now())
        read = await self._read(cmd.image_url)
        if is_food(read):
            reply = {"route": "food", "meal": await self._save_as_meal(cmd, read)}
            if reservation is not None:
                async with self.uow_factory() as uow:
                    await complete(uow, reservation, reply)
            return reply
        async with self.uow_factory() as uow:
            product = await self._known_product(uow, read, cmd.user_id)
            if product is None:
                product = product_from_label(
                    read,
                    product_id=str(uuid4()),
                    owner_user_id=cmd.user_id,
                    label_image_url=cmd.image_url,
                )
                await uow.supplements.add_product(product)
            entry = await self._put_on_list(uow, cmd.user_id, product)
            intake = await self._log(
                uow,
                user_id=cmd.user_id,
                product=product,
                intake_id=cmd.intake_id,
                units=entry.units_per_dose,
                taken_at=cmd.taken_at,
                header_timezone=cmd.header_timezone,
            )
            doses = await self._day_doses(
                uow, cmd.user_id, product.id, intake.local_date
            )
            reply = {
                "route": "supplement",
                "product": product_payload(product, cmd.user_id),
                "entry": list_entry_payload(entry, product, cmd.user_id, doses),
                "intake": intake_payload(intake),
            }
            if reservation is not None:
                await complete(uow, reservation, reply)
        await self._purge(cmd.user_id, intake.local_date)
        return reply

    async def _read(self, image_url: str) -> SupplementLabelResponse:
        try:
            async with asyncio.timeout(READ_TIMEOUT_SECONDS):
                image = await self.download_image_bytes(image_url)
                result = await self.vision_service.analyze_with_strategy(
                    image, SupplementLabelAnalysisStrategy()
                )
            read = SupplementLabelResponse.model_validate(result.get("structured_data"))
        except TimeoutError as exc:
            raise ExternalServiceException(
                "Reading the label took too long", "SUPPLEMENT_LABEL_TIMEOUT"
            ) from exc
        except (
            AIOutputValidationError,
            RuntimeError,
            ValueError,
            TypeError,
            AttributeError,
        ) as exc:
            # A reply outside the contract means the photo could not be read.
            # Outages and refusals are not caught here; they keep their codes.
            raise _unreadable() from exc
        if not read.is_supplement_label or (
            not read.rows and read.macros_per_serving is None
        ):
            raise _unreadable()
        return read

    async def _known_product(
        self, uow: Any, read: SupplementLabelResponse, user_id: str
    ) -> SupplementProduct | None:
        """A barcode already in the catalog, or on the user's earlier scan, wins."""
        if not read.barcode:
            return None
        found = await uow.supplements.find_by_barcode(read.barcode, user_id)
        return None if found is None or found.macro_bearing else found

    async def _save_as_meal(
        self, cmd: ScanSupplementLabelCommand, read: SupplementLabelResponse
    ) -> dict[str, Any]:
        macros = read.macros_per_serving
        grams = read.serving_grams
        if macros is None or grams is None:
            # Without a serving weight the meal would be a guess.
            raise ValidationException(
                "This product is food; scan it as a food label",
                "SUPPLEMENT_LABEL_IS_FOOD",
            )
        async with self.uow_factory() as uow:
            existing = await uow.meals.find_by_id(cmd.intake_id)
            user_tz = await resolve_user_timezone_async(
                cmd.user_id, uow, cmd.header_timezone
            )
        if existing is not None:
            if existing.user_id != cmd.user_id:
                raise ConflictException(
                    "intake_id is already used", "SUPPLEMENT_INTAKE_ID_TAKEN"
                )
            return _meal_summary(existing)
        now = utc_now()
        taken_local = self._taken(cmd.taken_at, now).astimezone(get_zone_info(user_tz))
        per_100g = 100 / grams
        name = " ".join(part for part in (read.brand, read.product_name) if part)
        name = name[:_DISH_NAME_LIMIT]
        item = ManualMealItem(
            name=name,
            quantity=grams,
            unit="g",
            origin="custom",
            custom_nutrition=CustomNutrition(
                calories_per_100g=(macros.calories_kcal or 0) * per_100g,
                protein_per_100g=(macros.protein_g or 0) * per_100g,
                carbs_per_100g=(macros.carbs_g or 0) * per_100g,
                fat_per_100g=(macros.fat_g or 0) * per_100g,
            ),
        )
        meal = await self.event_bus.send(
            CreateManualMealCommand(
                user_id=cmd.user_id,
                items=[item],
                dish_name=name,
                meal_id=cmd.intake_id,
                target_date=taken_local.date(),
                source="food_label",
            )
        )
        return _meal_summary(meal)
