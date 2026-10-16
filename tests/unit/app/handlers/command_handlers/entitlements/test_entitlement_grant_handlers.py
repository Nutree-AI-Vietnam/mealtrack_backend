"""Admin grant create/revoke handlers (NM-680, D5)."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from tests.fixtures.fakes.fake_billing_provider import (
    NOW,
    FakeBillingProvider,
    FakeBudget,
    FakeClock,
    make_user,
)
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork

from src.api.exceptions import (
    ConflictException,
    ResourceNotFoundException,
    ValidationException,
)
from src.app.commands.entitlements import (
    CreateEntitlementGrantCommand,
    RevokeEntitlementGrantCommand,
)
from src.app.handlers.command_handlers.entitlements import (
    CreateEntitlementGrantCommandHandler,
    RevokeEntitlementGrantCommandHandler,
)
from src.app.handlers.command_handlers.entitlements import (
    create_entitlement_grant_command_handler as create_module,
)
from src.app.services.entitlement_sync_service import EntitlementSyncService

U1, U2 = "user-1", "user-2"


@pytest.fixture
def uow():
    store = FakeUnitOfWork()
    store.users.users[U1] = make_user(U1, "uid-1")
    store.users.users[U2] = make_user(U2, "uid-2")
    return store


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
def sync(uow, clock):
    return EntitlementSyncService(
        uow_factory=lambda: uow,
        provider=FakeBillingProvider(),
        budget=FakeBudget(),
        clock=clock,
    )


@pytest.fixture
def create(uow, sync, clock):
    ids = iter(f"grant-{n}" for n in range(1, 100))
    return CreateEntitlementGrantCommandHandler(
        uow_factory=lambda: uow,
        sync_service=sync,
        clock=clock,
        id_factory=lambda: next(ids),
    )


@pytest.fixture
def revoke(uow, sync, clock):
    return RevokeEntitlementGrantCommandHandler(
        uow_factory=lambda: uow, sync_service=sync, clock=clock
    )


def _command(**overrides) -> CreateEntitlementGrantCommand:
    values = {
        "user_id": U1,
        "source": "support",
        "reason": "Refund dispute goodwill",
        "starts_at": NOW,
        "ends_at": NOW + timedelta(days=30),
        "created_by": "admin@nutreeai.com",
    }
    values.update(overrides)
    return CreateEntitlementGrantCommand(**values)


@pytest.mark.asyncio
async def test_create_grant_gives_access_and_records_admin(create, uow):
    with patch.object(create_module, "increment_metric") as metric:
        result = await create.handle(_command())

    assert result.changed is True
    assert result.grant.id == "grant-1"
    assert result.grant.created_by == "admin@nutreeai.com"
    assert result.projection.has_access is True
    assert result.projection.primary_source == "grant"
    assert result.projection.access_until == NOW + timedelta(days=30)
    assert await uow.user_entitlements.get(U1) == result.projection
    assert uow.user_entitlements.lock_calls == [[U1]]
    metric.assert_called_once_with(
        "entitlement.grant.created",
        attributes={"source": "support", "entitlement_key": "standard"},
    )


@pytest.mark.asyncio
async def test_future_grant_schedules_next_change(create):
    result = await create.handle(
        _command(starts_at=NOW + timedelta(days=1), ends_at=NOW + timedelta(days=2))
    )

    assert result.projection.has_access is False
    assert result.projection.next_change_at == NOW + timedelta(days=1)


@pytest.mark.asyncio
async def test_source_ref_makes_create_idempotent(create, uow):
    first = await create.handle(_command(source_ref="ticket-42"))
    again = await create.handle(_command(source_ref="ticket-42"))

    assert again.changed is False
    assert again.grant == first.grant
    assert len(uow.entitlement_grants.rows) == 1


@pytest.mark.asyncio
async def test_source_ref_owned_by_another_user_conflicts(create):
    await create.handle(_command(source_ref="ticket-42"))

    with pytest.raises(ConflictException):
        await create.handle(_command(user_id=U2, source_ref="ticket-42"))


@pytest.mark.asyncio
async def test_create_for_unknown_or_inactive_user_is_not_found(create, uow):
    uow.users.users[U2] = make_user(U2, "uid-2", active=False)

    with pytest.raises(ResourceNotFoundException):
        await create.handle(_command(user_id="missing"))
    with pytest.raises(ResourceNotFoundException):
        await create.handle(_command(user_id=U2))


@pytest.mark.parametrize(
    "overrides",
    [
        {"ends_at": NOW + timedelta(days=367)},
        {"ends_at": NOW},
        {"source": "promo"},
        {"reason": "   "},
        {"reason": "x" * 256},
        {"created_by": ""},
        {"user_id": " "},
        {"starts_at": NOW.replace(tzinfo=None)},
    ],
)
def test_create_command_validates_d5_rules(overrides):
    with pytest.raises(ValidationException):
        _command(**overrides)


@pytest.mark.asyncio
async def test_create_defaults_start_to_the_handler_clock(create):
    # Not the wall clock: ends_at is only valid relative to the injected NOW.
    command = CreateEntitlementGrantCommand(
        user_id=U1,
        source="qa",
        reason="QA account",
        ends_at=NOW + timedelta(days=30),
        created_by="admin@nutreeai.com",
    )
    assert command.starts_at is None

    result = await create.handle(command)

    assert result.grant.starts_at == NOW
    assert result.projection.has_access is True


@pytest.mark.asyncio
async def test_create_rejects_an_end_before_the_handler_clock(create, uow):
    command = CreateEntitlementGrantCommand(
        user_id=U1,
        source="qa",
        reason="QA account",
        ends_at=NOW - timedelta(minutes=1),
        created_by="admin@nutreeai.com",
    )

    with pytest.raises(ValidationException):
        await create.handle(command)
    assert uow.entitlement_grants.rows == {}


@pytest.mark.asyncio
async def test_chained_grants_cannot_extend_access_past_366_days(create, uow):
    first = await create.handle(_command(ends_at=NOW + timedelta(days=366)))

    with pytest.raises(ValidationException, match="366 days from now"):
        await create.handle(
            _command(
                starts_at=first.grant.ends_at,
                ends_at=first.grant.ends_at + timedelta(days=366),
            )
        )
    with pytest.raises(ValidationException, match="366 days from now"):
        await create.handle(
            _command(
                starts_at=NOW + timedelta(days=200),
                ends_at=NOW + timedelta(days=400),
            )
        )
    assert len(uow.entitlement_grants.rows) == 1


@pytest.mark.asyncio
async def test_a_new_grant_may_extend_access_once_time_has_passed(create, uow, clock):
    await create.handle(_command(ends_at=NOW + timedelta(days=366)))
    clock.advance(days=30)

    later = await create.handle(
        _command(starts_at=clock.now, ends_at=clock.now + timedelta(days=366))
    )

    assert later.projection.access_until == NOW + timedelta(days=396)
    assert len(uow.entitlement_grants.rows) == 2


def test_create_command_allows_the_366_day_maximum():
    command = _command(ends_at=NOW + timedelta(days=366))
    assert command.ends_at - command.starts_at == timedelta(days=366)


@pytest.mark.asyncio
async def test_revoke_removes_access_and_is_idempotent(create, revoke, clock):
    created = await create.handle(_command())
    clock.advance(hours=1)

    revoked = await revoke.handle(
        RevokeEntitlementGrantCommand(
            grant_id=created.grant.id, reason="Granted by mistake", revoked_by="a@b.c"
        )
    )
    again = await revoke.handle(
        RevokeEntitlementGrantCommand(
            grant_id=created.grant.id, reason="again", revoked_by="a@b.c"
        )
    )

    assert revoked.changed is True
    assert revoked.grant.revoked_at == NOW + timedelta(hours=1)
    assert revoked.grant.revoke_reason == "Granted by mistake"
    assert revoked.grant.revoked_by == "a@b.c"
    assert revoked.projection.has_access is False
    assert revoked.projection.state == "expired"
    assert again.changed is False
    assert again.grant.revoke_reason == "Granted by mistake"


@pytest.mark.asyncio
async def test_grant_audit_logs_never_carry_admin_emails(create, revoke, caplog):
    caplog.set_level("INFO")
    created = await create.handle(_command(created_by="Admin@NutreeAI.com"))
    await revoke.handle(
        RevokeEntitlementGrantCommand(
            grant_id=created.grant.id, reason="cleanup", revoked_by="Admin@NutreeAI.com"
        )
    )

    audit = [
        r for r in caplog.records if r.getMessage().startswith("Entitlement grant")
    ]
    assert [r.getMessage() for r in audit] == [
        "Entitlement grant created",
        "Entitlement grant revoked",
    ]
    for record in audit:
        assert "nutreeai.com" not in str(vars(record)).lower()
    # The revoke digest still lets an auditor match the allowlisted admin.
    assert len(audit[1].revoked_by_sha256) == 64


@pytest.mark.asyncio
async def test_revoke_unknown_grant_is_not_found(revoke):
    with pytest.raises(ResourceNotFoundException):
        await revoke.handle(
            RevokeEntitlementGrantCommand(
                grant_id="missing", reason="cleanup", revoked_by="a@b.c"
            )
        )


@pytest.mark.parametrize(
    "overrides",
    [{"grant_id": ""}, {"reason": " "}, {"reason": "x" * 256}, {"revoked_by": ""}],
)
def test_revoke_command_validates(overrides):
    values = {"grant_id": "g", "reason": "r", "revoked_by": "a@b.c", **overrides}
    with pytest.raises(ValidationException):
        RevokeEntitlementGrantCommand(**values)


@pytest.mark.asyncio
async def test_grant_on_other_key_never_fabricates_a_standard_row(create, uow):
    result = await create.handle(_command(entitlement_key="coach"))

    assert result.projection.entitlement_key == "coach"
    assert await uow.user_entitlements.get(U1, "standard") is None
