"""Writes to a person's supplement list and doses.

A dose stores its nutrient snapshot at log time, so later product edits never
change history. Writes that touch a day purge its caches before returning.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid4

from src.api.exceptions import (
    ConflictException,
    ResourceNotFoundException,
    ValidationException,
)
from src.app.commands.supplement import (
    AddSupplementCommand,
    DeleteSupplementIntakeCommand,
    LogSupplementIntakeCommand,
    RemoveSupplementCommand,
    UpdateSupplementCommand,
)
from src.app.events.base import EventHandler, handles
from src.app.services.describe_log_replay import MIN_LEASE_SECONDS, complete, run_once
from src.app.services.supplement_days import dose_day
from src.app.services.supplement_payloads import intake_payload, list_entry_payload
from src.domain.model.supplement import (
    SupplementIntake,
    SupplementProduct,
    UserSupplement,
)
from src.domain.utils.timezone_utils import (
    get_zone_info,
    resolve_user_timezone_async,
    utc_now,
)

DayPurge = Callable[[str, date], Awaitable[None]]
# Phone clocks drift; a dose further ahead than this is a client bug.
_FUTURE_GRACE = timedelta(minutes=10)
# The diary goes back about a year; older times are client bugs.
_MAX_BACKDATE = timedelta(days=366)
ADD_SUPPLEMENT_OPERATION = "supplement_add"


def _not_found(what: str) -> ResourceNotFoundException:
    return ResourceNotFoundException(f"{what} not found", "SUPPLEMENT_NOT_FOUND")


class _SupplementWrite:
    def __init__(
        self, uow_factory: Callable[[], Any], purge_day: DayPurge | None = None
    ):
        self.uow_factory = uow_factory
        self.purge_day = purge_day

    async def _purge(self, user_id: str, day: date | None) -> None:
        if self.purge_day is not None and day is not None:
            await self.purge_day(user_id, day)

    async def _product(
        self, uow: Any, product_id: str, user_id: str
    ) -> SupplementProduct:
        product = await uow.supplements.get_product(product_id, user_id)
        if product is None:
            raise _not_found("Supplement")
        if product.macro_bearing:
            # Protein powders and shakes carry calories; the app logs them as food.
            raise ValidationException(
                "This product is logged as food", "SUPPLEMENT_IS_FOOD"
            )
        return product

    async def _log(
        self,
        uow: Any,
        *,
        user_id: str,
        product: SupplementProduct,
        intake_id: str,
        units: float,
        taken_at: datetime | None,
        header_timezone: str | None,
    ) -> SupplementIntake:
        now = utc_now()
        taken = taken_at or now
        if taken.tzinfo is None:
            taken = taken.replace(tzinfo=now.tzinfo)
        if not now - _MAX_BACKDATE <= taken <= now + _FUTURE_GRACE:
            raise ValidationException(
                "taken_at must be within the last year", "INVALID_TAKEN_AT"
            )
        user_tz = await resolve_user_timezone_async(user_id, uow, header_timezone)
        local_date, on_day = dose_day(taken, get_zone_info(user_tz), now)
        intake = SupplementIntake.log(
            id=intake_id,
            user_id=user_id,
            product=product,
            units=units,
            taken_at=taken,
            local_date=local_date,
            logged_on_day=on_day,
        )
        # A reused id never attaches the existing dose (client-id decision 260826).
        if not await uow.supplements.add_intake(intake):
            raise ConflictException(
                "intake_id is already used", "SUPPLEMENT_INTAKE_ID_TAKEN"
            )
        return intake


@handles(AddSupplementCommand)
class AddSupplementCommandHandler(
    _SupplementWrite, EventHandler[AddSupplementCommand, dict]
):
    async def handle(self, cmd: AddSupplementCommand) -> dict:
        if cmd.log_now and not cmd.intake_id:
            raise ValidationException(
                "log_now needs an intake_id", "SUPPLEMENT_INTAKE_ID_REQUIRED"
            )
        return await run_once(
            self.uow_factory,
            cmd.user_id,
            cmd.replay_key,
            lease_seconds=MIN_LEASE_SECONDS,
            log=lambda reservation: self._add(cmd, reservation),
        )

    async def _add(self, cmd: AddSupplementCommand, reservation: Any) -> dict:
        async with self.uow_factory() as uow:
            repo = uow.supplements
            product = await self._product(uow, cmd.product_id, cmd.user_id)
            listed = {e.product_id: e for e in await repo.list_supplements(cmd.user_id)}
            current = listed.get(product.id)
            entry = await repo.save_list_entry(
                UserSupplement(
                    id=current.id if current else str(uuid4()),
                    user_id=cmd.user_id,
                    product_id=product.id,
                    units_per_dose=cmd.units_per_dose
                    or (
                        current.units_per_dose if current else product.units_per_serving
                    ),
                    sort_order=current.sort_order if current else len(listed),
                )
            )
            intake = None
            if cmd.log_now and cmd.intake_id:
                intake = await self._log(
                    uow,
                    user_id=cmd.user_id,
                    product=product,
                    intake_id=cmd.intake_id,
                    units=entry.units_per_dose,
                    taken_at=cmd.taken_at,
                    header_timezone=cmd.header_timezone,
                )
                day = intake.local_date
            else:
                user_tz = await resolve_user_timezone_async(
                    cmd.user_id, uow, cmd.header_timezone
                )
                day = utc_now().astimezone(get_zone_info(user_tz)).date()
            # The row carries the whole day's doses, as GET /mine does.
            doses = [
                dose
                for dose in await repo.intakes_between(cmd.user_id, day, day)
                if dose.product_id == product.id
            ]
            reply = {
                "entry": list_entry_payload(entry, product, cmd.user_id, doses),
                "intake": intake_payload(intake) if intake else None,
            }
            if reservation is not None:
                await complete(uow, reservation, reply)
        await self._purge(cmd.user_id, intake.local_date if intake else None)
        return reply


@handles(UpdateSupplementCommand)
class UpdateSupplementCommandHandler(
    _SupplementWrite, EventHandler[UpdateSupplementCommand, dict]
):
    async def handle(self, cmd: UpdateSupplementCommand) -> dict:
        async with self.uow_factory() as uow:
            entry = await uow.supplements.update_list_entry(
                cmd.user_id,
                cmd.entry_id,
                units_per_dose=cmd.units_per_dose,
                active=cmd.active,
                sort_order=cmd.sort_order,
            )
            if entry is None:
                raise _not_found("Supplement list row")
            product = (
                await uow.supplements.products_for(cmd.user_id, [entry.product_id])
            ).get(entry.product_id)
            if product is None:
                raise _not_found("Supplement")
        return list_entry_payload(entry, product, cmd.user_id)


@handles(RemoveSupplementCommand)
class RemoveSupplementCommandHandler(
    _SupplementWrite, EventHandler[RemoveSupplementCommand, dict]
):
    async def handle(self, cmd: RemoveSupplementCommand) -> dict:
        async with self.uow_factory() as uow:
            if not await uow.supplements.remove_list_entry(cmd.user_id, cmd.entry_id):
                raise _not_found("Supplement list row")
        return {"id": cmd.entry_id, "removed": True}


@handles(LogSupplementIntakeCommand)
class LogSupplementIntakeCommandHandler(
    _SupplementWrite, EventHandler[LogSupplementIntakeCommand, dict]
):
    async def handle(self, cmd: LogSupplementIntakeCommand) -> dict:
        async def log(reservation: Any) -> dict:
            async with self.uow_factory() as uow:
                product = await self._product(uow, cmd.product_id, cmd.user_id)
                intake = await self._log(
                    uow,
                    user_id=cmd.user_id,
                    product=product,
                    intake_id=cmd.intake_id,
                    units=cmd.units,
                    taken_at=cmd.taken_at,
                    header_timezone=cmd.header_timezone,
                )
                reply = intake_payload(intake)
                if reservation is not None:
                    # Stored with the dose, so a retry with the same key replays it.
                    await complete(uow, reservation, reply)
            await self._purge(cmd.user_id, intake.local_date)
            return reply

        return await run_once(
            self.uow_factory,
            cmd.user_id,
            cmd.replay_key,
            lease_seconds=MIN_LEASE_SECONDS,
            log=log,
        )


@handles(DeleteSupplementIntakeCommand)
class DeleteSupplementIntakeCommandHandler(
    _SupplementWrite, EventHandler[DeleteSupplementIntakeCommand, dict]
):
    async def handle(self, cmd: DeleteSupplementIntakeCommand) -> dict:
        async with self.uow_factory() as uow:
            deleted = await uow.supplements.soft_delete_intake(
                cmd.user_id, cmd.intake_id, utc_now()
            )
            if deleted is None:
                raise _not_found("Supplement dose")
        await self._purge(cmd.user_id, deleted.local_date)
        return {"id": cmd.intake_id, "deleted": True}
