"""Supplement products, lists and doses on real PostgreSQL.

The visibility rule (shared and approved, or your own) lives in SQL, so it
is checked here rather than against a mocked session.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from src.domain.model.supplement import (
    ProductSource,
    ReviewStatus,
    SupplementForm,
    SupplementIntake,
    SupplementNutrients,
    SupplementProduct,
    SupplementProductNotFoundError,
    UserSupplement,
)
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork

pytestmark = pytest.mark.integration

BARCODE = "9300807362341"


@pytest_asyncio.fixture
async def factory(monkeypatch, pg_session, async_session_factory):
    monkeypatch.setattr(
        "src.infra.database.uow_async.AsyncSessionLocal", async_session_factory
    )
    # Shared products have no owner, so truncating users does not reach them.
    await pg_session.execute(text("TRUNCATE TABLE supplement_products CASCADE"))
    await pg_session.commit()
    yield async_session_factory
    await pg_session.execute(text("TRUNCATE TABLE supplement_products CASCADE"))
    await pg_session.commit()


async def _user(session) -> str:
    user = User(
        id=str(uuid4()),
        firebase_uid=f"uid-{uuid4()}",
        email=f"{uuid4()}@example.com",
        username=f"user-{uuid4()}",
        password_hash="unused",
    )
    session.add(user)
    await session.commit()
    return user.id


def _product(name: str, owner: str | None = None, **overrides) -> SupplementProduct:
    fields = {
        "id": str(uuid4()),
        "name": name,
        "brand": "Blackmores",
        "form": SupplementForm.TABLET,
        "unit_label": "viên",
        "source": ProductSource.CURATED if owner is None else ProductSource.LABEL_SCAN,
        "review_status": ReviewStatus.APPROVED
        if owner is None
        else ReviewStatus.PRIVATE,
        "owner_user_id": owner,
        "nutrients": SupplementNutrients({"vitamin_c": 500.0}),
    }
    return SupplementProduct(**{**fields, **overrides})


async def _add(*products: SupplementProduct) -> None:
    async with AsyncUnitOfWork() as uow:
        for product in products:
            await uow.supplements.add_product(product)
        await uow.commit()


@pytest.mark.asyncio
async def test_schema_is_ready_after_migration(factory):
    async with AsyncUnitOfWork() as uow:
        assert await uow.supplements.schema_ready() is True


@pytest.mark.asyncio
async def test_another_users_private_product_is_invisible_by_id_barcode_and_search(
    factory, pg_session
):
    alice, bob = await _user(pg_session), await _user(pg_session)
    private = _product("Bio C 1000 scan", owner=alice, barcodes=(BARCODE,))
    pending = _product("Bio C 1000 pending", review_status=ReviewStatus.PENDING_REVIEW)
    await _add(private, pending)

    async with AsyncUnitOfWork() as uow:
        repo = uow.supplements
        assert (await repo.get_product(private.id, alice)).id == private.id
        assert await repo.get_product(private.id, bob) is None
        assert (await repo.find_by_barcode(BARCODE, alice)).id == private.id
        assert await repo.find_by_barcode(BARCODE, bob) is None
        assert [p.id for p in await repo.search_products("bio c", alice)] == [
            private.id
        ]
        assert await repo.search_products("bio c", bob) == []
        # Shared rows waiting for review are invisible to everyone.
        assert await repo.get_product(pending.id, alice) is None


@pytest.mark.asyncio
async def test_barcode_lookup_prefers_the_shared_product_and_accepts_any_gtin(
    factory, pg_session
):
    alice = await _user(pg_session)
    shared = _product("Bio C 1000", barcodes=(BARCODE,), sold_in_vn=True)
    own = _product("Bio C 1000 (my scan)", owner=alice, barcodes=(BARCODE,))
    await _add(shared, own)

    async with AsyncUnitOfWork() as uow:
        found = await uow.supplements.find_by_barcode("0" + BARCODE, alice)

    assert found.id == shared.id
    assert found.barcodes == ("0" + BARCODE,)


@pytest.mark.asyncio
async def test_search_matches_every_word_and_ranks_products_sold_in_vietnam_first(
    factory, pg_session
):
    alice = await _user(pg_session)
    abroad = _product("Vitamin D3 1000IU", brand="Nature Made")
    local = _product("Vitamin D3 1000IU", brand="Ostelin", sold_in_vn=True)
    other = _product("Fish Oil 1000mg", brand="Ostelin", sold_in_vn=True)
    await _add(abroad, local, other)

    async with AsyncUnitOfWork() as uow:
        found = await uow.supplements.search_products("d3 1000", alice)
        # Folding drops punctuation, so a lone LIKE wildcard matches nothing.
        wildcard = await uow.supplements.search_products("%", alice)

    assert [p.id for p in found] == [local.id, abroad.id]
    assert wildcard == []


@pytest.mark.asyncio
async def test_vietnamese_nutrient_names_find_english_label_names(factory, pg_session):
    alice = await _user(pg_session)
    iron = _product("Iron 18 mg", brand="NOW")
    satiety = _product("PureLean Satiety", brand="Pure Encapsulations")
    fish_oil = _product("Omega-3 Fish Oil", brand="NOW")
    calcium = _product("Calcium Citrate", brand="Solgar")
    canxi = _product("Canxi Nano", brand="Hataphar", sold_in_vn=True)
    alkemy = _product("Fast-C Alkemy", brand="Bronson")
    zinc = _product("Zinc 50 mg", brand="NOW")
    await _add(iron, satiety, fish_oil, calcium, canxi, alkemy, zinc)

    async with AsyncUnitOfWork() as uow:
        repo = uow.supplements

        async def names(query: str) -> set[str]:
            return {p.name for p in await repo.search_products(query, alice)}

        assert await names("sắt") == {"Iron 18 mg"}
        assert await names("dầu cá") == {"Omega-3 Fish Oil"}
        assert await names("canxi") == {"Calcium Citrate", "Canxi Nano"}
        assert await names("kẽm") == {"Zinc 50 mg"}
        assert await names("now sắt") == {"Iron 18 mg"}


@pytest.mark.asyncio
async def test_list_rows_upsert_per_product_and_delete_only_for_their_owner(
    factory, pg_session
):
    alice, bob = await _user(pg_session), await _user(pg_session)
    product = _product("Fish Oil 1000mg")
    await _add(product)

    async with AsyncUnitOfWork() as uow:
        first = await uow.supplements.save_list_entry(
            UserSupplement(str(uuid4()), alice, product.id, units_per_dose=1)
        )
        again = await uow.supplements.save_list_entry(
            UserSupplement(str(uuid4()), alice, product.id, units_per_dose=2)
        )
        await uow.commit()
    assert again.id == first.id and again.units_per_dose == 2

    async with AsyncUnitOfWork() as uow:
        assert await uow.supplements.remove_list_entry(bob, first.id) is False
        assert await uow.supplements.remove_list_entry(alice, first.id) is True
        assert await uow.supplements.list_supplements(alice) == []


@pytest.mark.asyncio
async def test_a_reused_intake_id_is_refused_and_deletes_are_scoped_to_the_user(
    factory, pg_session
):
    alice, bob = await _user(pg_session), await _user(pg_session)
    product = _product("Fish Oil 1000mg")
    await _add(product)
    intake = SupplementIntake.log(
        id=str(uuid4()),
        user_id=alice,
        product=product,
        units=2,
        taken_at=datetime(2026, 11, 1, 1, 0, tzinfo=UTC),
        local_date=date(2026, 11, 1),
        logged_on_day=True,
    )

    async with AsyncUnitOfWork() as uow:
        assert await uow.supplements.add_intake(intake) is True
        assert await uow.supplements.add_intake(intake) is False
        await uow.commit()

    async with AsyncUnitOfWork() as uow:
        repo = uow.supplements
        [stored] = await repo.intakes_between(
            alice, date(2026, 11, 1), date(2026, 11, 1)
        )
        assert stored.nutrients.amounts == {"vitamin_c": 1000.0}
        assert (
            await repo.intakes_between(alice, date(2026, 11, 2), date(2026, 11, 8))
            == []
        )
        now = datetime.now(UTC)
        assert await repo.soft_delete_intake(bob, intake.id, now) is None
        deleted = await repo.soft_delete_intake(alice, intake.id, now)
        assert deleted.local_date == date(2026, 11, 1)
        assert await repo.soft_delete_intake(alice, intake.id, now) is None
        assert (
            await repo.intakes_between(alice, date(2026, 11, 1), date(2026, 11, 1))
            == []
        )


@pytest.mark.asyncio
async def test_account_deletion_removes_doses_list_and_own_products_only(
    factory, pg_session
):
    alice, bob = await _user(pg_session), await _user(pg_session)
    shared = _product("Bio C 1000")
    own = _product("My gummy", owner=alice, barcodes=(BARCODE,))
    bobs = _product("Bob's gummy", owner=bob)
    await _add(shared, own, bobs)
    taken = datetime(2026, 11, 1, 1, 0, tzinfo=UTC)
    async with AsyncUnitOfWork() as uow:
        for user, product in ((alice, shared), (alice, own), (bob, shared)):
            await uow.supplements.save_list_entry(
                UserSupplement(str(uuid4()), user, product.id, units_per_dose=1)
            )
            await uow.supplements.add_intake(
                SupplementIntake.log(
                    id=str(uuid4()),
                    user_id=user,
                    product=product,
                    units=1,
                    taken_at=taken,
                    local_date=date(2026, 11, 1),
                    logged_on_day=True,
                )
            )
        await uow.commit()

    async with AsyncUnitOfWork() as uow:
        assert await uow.supplements.delete_user_data(alice) == 5
        await uow.commit()

    async with AsyncUnitOfWork() as uow:
        repo = uow.supplements
        assert await repo.list_supplements(alice) == []
        assert (
            await repo.intakes_between(alice, date(2026, 11, 1), date(2026, 11, 1))
            == []
        )
        assert len(await repo.list_supplements(bob)) == 1
        assert await repo.get_product(shared.id, bob) is not None
        assert await repo.get_product(bobs.id, bob) is not None
        assert await repo.find_by_barcode(BARCODE, alice) is None


@pytest.mark.asyncio
async def test_doses_and_list_rows_refuse_a_product_the_user_cannot_see(
    factory, pg_session
):
    alice, bob = await _user(pg_session), await _user(pg_session)
    private = _product("My gummy", owner=alice)
    await _add(private)

    async with AsyncUnitOfWork() as uow:
        with pytest.raises(SupplementProductNotFoundError):
            await uow.supplements.save_list_entry(
                UserSupplement(str(uuid4()), bob, private.id, units_per_dose=1)
            )
        with pytest.raises(SupplementProductNotFoundError):
            await uow.supplements.add_intake(
                SupplementIntake.log(
                    id=str(uuid4()),
                    user_id=bob,
                    product=private,
                    units=1,
                    taken_at=datetime(2026, 11, 1, 1, 0, tzinfo=UTC),
                    local_date=date(2026, 11, 1),
                    logged_on_day=True,
                )
            )
        with pytest.raises(ValueError):
            await uow.supplements.get_product(private.id, "")


@pytest.mark.asyncio
async def test_a_second_own_product_with_the_same_barcode_saves_without_it(
    factory, pg_session
):
    alice = await _user(pg_session)
    first = _product("Bio C scan", owner=alice, barcodes=(BARCODE,))
    again = _product("Bio C scan again", owner=alice, barcodes=(BARCODE,))

    async with AsyncUnitOfWork() as uow:
        assert await uow.supplements.add_product(first) == ("0" + BARCODE,)
        assert await uow.supplements.add_product(again) == ()
        await uow.commit()

    async with AsyncUnitOfWork() as uow:
        assert (await uow.supplements.find_by_barcode(BARCODE, alice)).id == first.id
        assert (await uow.supplements.get_product(again.id, alice)).barcodes == ()


@pytest.mark.asyncio
async def test_search_lists_approved_shared_rows_before_the_users_own(
    factory, pg_session
):
    alice = await _user(pg_session)
    own = _product("Vitamin C 500", owner=alice, brand="Ascorbin")
    shared = _product("Vitamin C 1000", brand="Zinc Co")
    await _add(own, shared)

    async with AsyncUnitOfWork() as uow:
        found = await uow.supplements.search_products("vitamin c", alice)

    assert [p.id for p in found] == [shared.id, own.id]


@pytest.mark.asyncio
async def test_the_day_fingerprint_moves_when_a_dose_is_added_or_deleted(
    factory, pg_session
):
    alice = await _user(pg_session)
    product = _product("Vitamin D3")
    await _add(product)
    day = date(2026, 11, 1)

    async with AsyncUnitOfWork() as uow:
        assert await uow.supplements.max_updated_at_for_local_day(day, alice) is None
        intake = SupplementIntake.log(
            id=str(uuid4()),
            user_id=alice,
            product=product,
            units=1,
            taken_at=datetime(2026, 11, 1, 1, 0, tzinfo=UTC),
            local_date=day,
            logged_on_day=True,
        )
        await uow.supplements.add_intake(intake)
        await uow.commit()

    async with AsyncUnitOfWork() as uow:
        added = await uow.supplements.max_updated_at_for_local_day(day, alice)
        await uow.supplements.soft_delete_intake(
            alice, intake.id, datetime(2030, 1, 1, tzinfo=UTC)
        )
        await uow.commit()

    async with AsyncUnitOfWork() as uow:
        deleted = await uow.supplements.max_updated_at_for_local_day(day, alice)

    assert added is not None and deleted > added
