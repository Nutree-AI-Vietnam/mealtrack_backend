"""Short durable job admission and renewable fenced leases."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.domain.ports.catalog_preparation_port import PreparationClaim, PreparationTask
from src.infra.database.models.meal_recommendation.catalog_preparation import (
    CatalogPreparationJobORM as Job,
)


class CatalogPreparationClaims:
    def __init__(self, session):
        self.session = session

    async def enqueue(self, values):
        if not values:
            return 0
        if len(values) > 4000:
            raise ValueError("Preparation insertion batch exceeds 4000 jobs")
        insert = (
            sqlite_insert
            if self.session.get_bind().dialect.name == "sqlite"
            else pg_insert
        )
        statement = (
            insert(Job)
            .values([{**value, "id": str(uuid4())} for value in values])
            .on_conflict_do_nothing(
                index_elements=[
                    "task",
                    "catalog_meal_id",
                    "input_facet_version",
                    "locale",
                    "contract_version",
                ]
            )
        )
        result = await self.session.execute(statement)
        return result.rowcount

    async def claim_next(self, *, lease_seconds=120, global_capacity=4):
        if not 10 <= lease_seconds <= 3600 or not 1 <= global_capacity <= 64:
            raise ValueError("Invalid preparation lease/capacity")
        if self.session.get_bind().dialect.name == "postgresql":
            # All replicas serialize the short admission decision, then release
            # the transaction before doing provider work.
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(707426541917239)")
            )
        now = (await self.session.execute(select(func.clock_timestamp()))).scalar_one()
        await self.session.execute(
            update(Job)
            .where(
                Job.attempts >= Job.max_attempts,
                or_(
                    Job.status.in_(("pending", "retry_wait")),
                    and_(Job.status == "running", Job.lease_expires_at <= now),
                ),
            )
            .values(
                status="failed",
                claim_token=None,
                lease_expires_at=None,
                last_error_code="retry_exhausted",
            )
        )
        running = (
            await self.session.execute(
                select(func.count())
                .select_from(Job)
                .where(Job.status == "running", Job.lease_expires_at > now)
            )
        ).scalar_one()
        if running >= global_capacity:
            return None
        row = (
            await self.session.execute(
                select(Job)
                .where(
                    Job.attempts < Job.max_attempts,
                    or_(
                        and_(
                            Job.status.in_(("pending", "retry_wait")),
                            Job.available_at <= now,
                        ),
                        and_(Job.status == "running", Job.lease_expires_at <= now),
                    ),
                )
                .order_by(Job.available_at, Job.created_at, Job.id)
                .limit(1)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        row.status = "running"
        row.claim_token = str(uuid4())
        row.lease_expires_at = now + timedelta(seconds=lease_seconds)
        row.attempts += 1
        row.last_error_code = None
        await self.session.flush()
        return PreparationClaim(
            row.id,
            PreparationTask(row.task),
            row.catalog_meal_id,
            row.input_facet_version,
            row.locale,
            row.contract_version,
            row.claim_token,
            row.attempts,
            row.max_attempts,
            row.lease_expires_at,
            row.created_at,
        )

    async def heartbeat(self, claim, *, lease_seconds=120):
        if not 10 <= lease_seconds <= 3600:
            raise ValueError("Invalid preparation lease")
        result = await self.session.execute(
            update(Job)
            .where(*self.fence(claim))
            .values(
                lease_expires_at=func.clock_timestamp()
                + timedelta(seconds=lease_seconds)
            )
        )
        return result.rowcount == 1

    @staticmethod
    def fence(claim):
        return (
            Job.id == claim.id,
            Job.status == "running",
            Job.claim_token == claim.claim_token,
            Job.lease_expires_at > func.clock_timestamp(),
        )

    async def locked_claim(self, claim):
        return (
            await self.session.execute(
                select(Job)
                .where(*self.fence(claim))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
