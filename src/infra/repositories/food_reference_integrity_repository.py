"""Transactional repository for materialized nutrition-integrity state."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.domain.services.nutrition_integrity_state import (
    IntegrityState,
    IntegrityStateError,
    NutritionIntegrityStateMachine,
    UnsupportedIntegrityPolicy,
    build_integrity_input_digest,
    deterministic_integrity_lock_ids,
    ensure_supported_policy_version,
)
from src.infra.database.models.food_reference_model import FoodReferenceModel
from src.infra.database.models.nutrition_integrity import (
    FoodReferenceIntegrityControlModel,
    FoodReferenceIntegrityEventModel,
)


class IntegrityControlUnavailableError(RuntimeError):
    """Raised when the DB has no authoritative policy control row."""


class UnsupportedIntegrityPolicyError(RuntimeError):
    """Raised when a rolling replica activates an unknown policy version."""


@dataclass(frozen=True)
class IntegrityControl:
    active_policy_version: str
    catalog_integrity_generation: int
    activation_run_id: str | None = None
    deployed_revision: str | None = None
    updated_at: datetime | None = None


class FoodReferenceIntegrityRepository:
    """Own policy control, eligibility reads, and forward state transitions."""

    def __init__(self, session: AsyncSession):
        self._session = session
        self._machine = NutritionIntegrityStateMachine()

    async def get_active_control(self, *, for_update: bool = False) -> IntegrityControl:
        statement = select(FoodReferenceIntegrityControlModel).where(
            FoodReferenceIntegrityControlModel.id == 1
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        if row is None:
            raise IntegrityControlUnavailableError(
                "food reference integrity control row is unavailable"
            )
        try:
            ensure_supported_policy_version(row.active_policy_version)
        except UnsupportedIntegrityPolicy as exc:
            raise UnsupportedIntegrityPolicyError(str(exc)) from exc
        return IntegrityControl(
            active_policy_version=row.active_policy_version,
            catalog_integrity_generation=int(row.catalog_integrity_generation),
            activation_run_id=row.activation_run_id,
            deployed_revision=row.deployed_revision,
            updated_at=row.updated_at,
        )

    async def lock_references(self, food_reference_ids: list[int]) -> tuple[int, ...]:
        """Acquire the shared transaction lock in deterministic ID order."""
        ordered_ids = deterministic_integrity_lock_ids(food_reference_ids)
        bind = getattr(self._session, "bind", None)
        if getattr(getattr(bind, "dialect", None), "name", None) == "postgresql":
            for reference_id in ordered_ids:
                await self._session.execute(
                    text("SELECT pg_advisory_xact_lock(:reference_id)"),
                    {"reference_id": reference_id},
                )
        return ordered_ids

    def public_eligibility_clause(self):
        control_exists = (
            select(FoodReferenceIntegrityControlModel.id)
            .where(FoodReferenceIntegrityControlModel.id == 1)
            .exists()
        )
        control_pending = (
            select(FoodReferenceIntegrityControlModel.id)
            .where(
                FoodReferenceIntegrityControlModel.id == 1,
                FoodReferenceIntegrityControlModel.activation_run_id.is_(None),
            )
            .exists()
        )
        control_activated = (
            select(FoodReferenceIntegrityControlModel.id)
            .where(
                FoodReferenceIntegrityControlModel.id == 1,
                FoodReferenceIntegrityControlModel.activation_run_id.is_not(None),
            )
            .exists()
        )
        active_policy = (
            select(FoodReferenceIntegrityControlModel.active_policy_version)
            .where(FoodReferenceIntegrityControlModel.id == 1)
            .scalar_subquery()
        )
        return and_(
            FoodReferenceModel.is_verified.is_(True),
            or_(
                # Migration creates a pending control row. Preserve the
                # pre-cutover verified read contract until the cohort has
                # been classified and activate_policy() commits the gate.
                and_(control_exists, control_pending),
                and_(
                    control_activated,
                    FoodReferenceModel.integrity_status == "valid",
                    FoodReferenceModel.integrity_policy_version == active_policy,
                ),
            ),
        )

    async def eligible_reference_ids(self, ids: list[int] | None = None) -> set[int]:
        """Read public IDs only when the DB control row and materialized state agree."""
        await self.get_active_control()
        statement = select(FoodReferenceModel.id).where(
            self.public_eligibility_clause()
        )
        if ids:
            statement = statement.where(FoodReferenceModel.id.in_(sorted(set(ids))))
        result = await self._session.execute(statement)
        return {int(value) for value in result.scalars().all()}

    async def audit_summary(self) -> dict[str, Any]:
        """Return bounded aggregate evidence without nutrition payloads."""
        control = await self.get_active_control()
        result = await self._session.execute(
            select(
                FoodReferenceModel.integrity_status,
                FoodReferenceModel.integrity_policy_version,
                func.count(FoodReferenceModel.id),
            )
            .group_by(
                FoodReferenceModel.integrity_status,
                FoodReferenceModel.integrity_policy_version,
            )
            .order_by(
                FoodReferenceModel.integrity_status,
                FoodReferenceModel.integrity_policy_version,
            )
        )
        states = [
            {
                "status": status,
                "policy_version": policy_version,
                "count": int(count),
            }
            for status, policy_version, count in result.all()
        ]
        return {
            "active_policy_version": control.active_policy_version,
            "catalog_integrity_generation": control.catalog_integrity_generation,
            "states": states,
        }

    async def materialize_reference(
        self,
        model: FoodReferenceModel,
        *,
        actor_kind: str = "system",
        reason_code: str = "writer_revalidated",
        deployed_revision: str | None = None,
    ) -> IntegrityState:
        """Classify a writer's row inside the same unit of work."""
        control = await self.get_active_control(for_update=True)
        await self.lock_references([model.id])
        digest = food_reference_integrity_digest(model)
        next_state = self._machine.classify(
            food_reference_integrity_payload(model),
            is_verified=bool(model.is_verified),
            active_policy_version=control.active_policy_version,
            input_digest=digest,
        )
        serving_rows = getattr(model, "serving_size_rows", [])
        if next_state.status == "valid" and not serving_rows:
            next_state = IntegrityState(
                status="quarantined",
                policy_version=next_state.policy_version,
                reason_code="serving_rows_required",
                input_digest=digest,
                review_reference=next_state.review_reference,
            )
        elif next_state.status == "valid" and model.serving_sizes is not None:
            # V1 reads normalized child rows only; clearing the legacy JSON is
            # part of the same materialization transaction.
            model.serving_sizes = None
            digest = food_reference_integrity_digest(model)
            next_state = replace(next_state, input_digest=digest)
        before_status = getattr(model, "integrity_status", "unknown")
        before_version = getattr(model, "integrity_policy_version", None)
        before_digest = getattr(model, "integrity_input_digest", None)
        model.integrity_status = next_state.status
        model.integrity_policy_version = next_state.policy_version
        model.integrity_checked_at = datetime.now().astimezone()
        model.integrity_reason = next_state.reason_code
        model.integrity_input_digest = next_state.input_digest
        model.integrity_review_reference = next_state.review_reference
        if (
            before_status != next_state.status
            or before_version != next_state.policy_version
            or before_digest != next_state.input_digest
        ):
            self._session.add(
                FoodReferenceIntegrityEventModel(
                    food_reference_id=model.id,
                    before_status=before_status,
                    after_status=next_state.status,
                    reason_code=reason_code,
                    policy_version=next_state.policy_version,
                    input_digest=next_state.input_digest,
                    actor_kind=actor_kind,
                    run_id=str(uuid4()),
                    deployed_revision=deployed_revision,
                )
            )
            control_row = await self._control_row_for_update()
            control_row.catalog_integrity_generation += 1
        await self._session.flush()
        return next_state

    async def quarantine_reference(
        self,
        food_reference_id: int,
        *,
        expected_input_digest: str,
        reason_code: str,
        review_reference: str,
        actor_kind: str = "system",
        reviewer_principal_hmac: str | None = None,
        run_id: str | None = None,
        operation_id: str | None = None,
        manifest_sha256: str | None = None,
        deployed_revision: str | None = None,
    ) -> IntegrityState:
        return await self._transition(
            food_reference_id,
            expected_input_digest=expected_input_digest,
            target="quarantined",
            reason_code=reason_code,
            review_reference=review_reference,
            actor_kind=actor_kind,
            reviewer_principal_hmac=reviewer_principal_hmac,
            run_id=run_id,
            operation_id=operation_id,
            manifest_sha256=manifest_sha256,
            deployed_revision=deployed_revision,
        )

    async def restore_reference(
        self,
        food_reference_id: int,
        *,
        expected_input_digest: str,
        review_reference: str,
        actor_kind: str = "system",
        reviewer_principal_hmac: str | None = None,
        run_id: str | None = None,
        operation_id: str | None = None,
        manifest_sha256: str | None = None,
        deployed_revision: str | None = None,
    ) -> IntegrityState:
        return await self._transition(
            food_reference_id,
            expected_input_digest=expected_input_digest,
            target="valid",
            reason_code="restored",
            review_reference=review_reference,
            actor_kind=actor_kind,
            reviewer_principal_hmac=reviewer_principal_hmac,
            run_id=run_id,
            operation_id=operation_id,
            manifest_sha256=manifest_sha256,
            deployed_revision=deployed_revision,
        )

    async def activate_policy(
        self,
        policy_version: str,
        *,
        activation_run_id: str,
        deployed_revision: str,
    ) -> IntegrityControl:
        """Atomically activate only a completely classified cohort."""
        ensure_supported_policy_version(policy_version)
        control_result = await self._session.execute(
            select(FoodReferenceIntegrityControlModel)
            .where(FoodReferenceIntegrityControlModel.id == 1)
            .with_for_update()
        )
        control = control_result.scalar_one_or_none()
        if control is None:
            raise IntegrityControlUnavailableError(
                "food reference integrity control row is unavailable"
            )
        incomplete = await self._session.scalar(
            select(func.count(FoodReferenceModel.id)).where(
                FoodReferenceModel.is_verified.is_(True),
                ~and_(
                    FoodReferenceModel.integrity_status.in_(("valid", "quarantined")),
                    FoodReferenceModel.integrity_policy_version == policy_version,
                ),
            )
        )
        if int(incomplete or 0):
            raise IntegrityStateError("integrity cohort is not completely classified")
        control.active_policy_version = policy_version
        control.activation_run_id = activation_run_id
        control.deployed_revision = deployed_revision
        control.catalog_integrity_generation += 1
        await self._session.flush()
        return await self.get_active_control()

    async def _transition(
        self,
        food_reference_id: int,
        *,
        expected_input_digest: str,
        target: str,
        reason_code: str,
        review_reference: str,
        actor_kind: str,
        reviewer_principal_hmac: str | None,
        run_id: str | None,
        operation_id: str | None,
        manifest_sha256: str | None,
        deployed_revision: str | None,
    ) -> IntegrityState:
        control = await self.get_active_control(for_update=True)
        await self.lock_references([food_reference_id])
        result = await self._session.execute(
            select(FoodReferenceModel)
            .where(FoodReferenceModel.id == food_reference_id)
            .options(
                selectinload(FoodReferenceModel.serving_size_rows),
                selectinload(FoodReferenceModel.nutrient_rows),
            )
            .with_for_update()
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise LookupError(f"food reference not found: {food_reference_id}")

        current_digest = food_reference_integrity_digest(model)
        if current_digest != expected_input_digest:
            raise IntegrityStateError("integrity input changed during transition")
        current = IntegrityState(
            status=getattr(model, "integrity_status", "unknown"),
            policy_version=getattr(model, "integrity_policy_version", None),
            reason_code=getattr(model, "integrity_reason", None),
            input_digest=current_digest,
            review_reference=getattr(model, "integrity_review_reference", None),
        )
        if target == "quarantined":
            next_state = self._machine.quarantine(
                current,
                expected_input_digest=expected_input_digest,
                reason_code=reason_code,
                review_reference=review_reference,
            )
        elif target == "valid":
            next_state = self._machine.restore(
                current,
                food_reference_integrity_payload(model),
                active_policy_version=control.active_policy_version,
                input_digest=current_digest,
                expected_input_digest=expected_input_digest,
                review_reference=review_reference,
            )
        else:
            raise IntegrityStateError(f"unsupported transition target: {target}")

        before_status = current.status
        model.integrity_status = next_state.status
        model.integrity_policy_version = next_state.policy_version
        model.integrity_checked_at = datetime.now().astimezone()
        model.integrity_reason = next_state.reason_code
        model.integrity_input_digest = next_state.input_digest
        model.integrity_review_reference = next_state.review_reference
        self._session.add(
            FoodReferenceIntegrityEventModel(
                food_reference_id=food_reference_id,
                before_status=before_status,
                after_status=next_state.status,
                reason_code=reason_code,
                policy_version=next_state.policy_version,
                input_digest=next_state.input_digest,
                actor_kind=actor_kind,
                reviewer_principal_hmac=reviewer_principal_hmac,
                approval_reference=review_reference,
                run_id=run_id or str(uuid4()),
                operation_id=operation_id,
                manifest_sha256=manifest_sha256,
                deployed_revision=deployed_revision,
            )
        )
        control_row = await self._control_row_for_update()
        control_row.catalog_integrity_generation += 1
        await self._session.flush()
        return next_state

    async def _control_row_for_update(self) -> FoodReferenceIntegrityControlModel:
        result = await self._session.execute(
            select(FoodReferenceIntegrityControlModel)
            .where(FoodReferenceIntegrityControlModel.id == 1)
            .with_for_update()
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise IntegrityControlUnavailableError(
                "food reference integrity control row is unavailable"
            )
        return row


def food_reference_integrity_payload(model: FoodReferenceModel) -> dict[str, Any]:
    return {
        "protein_100g": model.protein_100g,
        "carbs_100g": model.carbs_100g,
        "fat_100g": model.fat_100g,
        "fiber_100g": model.fiber_100g,
        "sugar_100g": model.sugar_100g,
        "serving_sizes": model.serving_sizes,
        "serving_options": [
            {"unit": row.name, "gram_weight": row.grams}
            for row in getattr(model, "serving_size_rows", [])
            if row.grams is not None
        ],
    }


def food_reference_integrity_digest(model: FoodReferenceModel) -> str:
    parent = {
        "id": model.id,
        "name": model.name,
        "barcode": model.barcode,
        "fdc_id": model.fdc_id,
        "source": model.source,
        "source_namespace": getattr(model, "source_namespace", None),
        "source_food_id": getattr(model, "source_food_id", None),
        "protein_100g": model.protein_100g,
        "carbs_100g": model.carbs_100g,
        "fat_100g": model.fat_100g,
        "fiber_100g": model.fiber_100g,
        "sugar_100g": model.sugar_100g,
        "density": model.density,
    }
    rows = [
        {
            "id": row.id,
            "position": row.position,
            "name": row.name,
            "grams": row.grams,
            "milliliters": row.milliliters,
            "is_default": row.is_default,
        }
        for row in getattr(model, "serving_size_rows", [])
    ]
    return build_integrity_input_digest(
        parent=parent,
        legacy_serving_sizes=model.serving_sizes,
        serving_rows=rows,
    )


__all__ = [
    "FoodReferenceIntegrityRepository",
    "food_reference_integrity_digest",
    "food_reference_integrity_payload",
    "IntegrityControl",
    "IntegrityControlUnavailableError",
    "UnsupportedIntegrityPolicyError",
]
