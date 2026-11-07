"""Batch micronutrient overlays match per-recipe reads and stay one query."""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.app.services.catalog_recipe_micronutrient_enrichment_service import (
    CatalogRecipeMicronutrientEnrichmentService,
)
from src.domain.model.meal_recommendation.catalog_recipe import CatalogMeal
from src.domain.ports.catalog_preparation_port import PREPARATION_CONTRACT_VERSION
from src.infra.database.models.meal_recommendation.catalog_micronutrient_enrichment import (
    MealCatalogMicronutrientEnrichmentORM as Micro,
)
from src.infra.database.models.meal_recommendation.catalog_projection import (
    MealCatalogProjectionORM as Projection,
)
from src.infra.database.models.meal_recommendation.catalog_recipe import (
    MealCatalogORM as Source,
)
from src.infra.repositories.catalog_preparation_reads import CatalogPreparationReads

DIGEST = "d" * 64
STALE_DIGEST = "e" * 64
OTHER_USER_RECIPE = "other-user"
# Not a user column: catalog overlays are shared. This row is the recipe the
# caller did not ask for, and its estimate must not come back on another id.
OTHER_USER_IRON = 999.0


def _hash(seed: str) -> str:
    return (seed * 64)[:64]


def _domain_meal(recipe_id: str) -> CatalogMeal:
    return CatalogMeal(
        id=recipe_id,
        catalog_key=recipe_id,
        content_hash=_hash(f"domain-{recipe_id}"),
        name=recipe_id,
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal("1"),
        carbs_g=Decimal("1"),
        fat_g=Decimal("1"),
        fiber_g=Decimal("1"),
    )


def _source(recipe_id: str, *, active: bool = True) -> Source:
    return Source(
        id=recipe_id,
        catalog_key=recipe_id,
        content_hash=_hash(f"source-{recipe_id}"),
        name=recipe_id,
        cuisine="vietnamese",
        recipe_payload={},
        payload_digest=_hash(f"payload-{recipe_id}"),
        breakfast_eligible=True,
        is_active=active,
    )


def _projection(
    recipe_id: str,
    *,
    digest: str = DIGEST,
    schema_version: int = 1,
    enrichment_dirty: bool = False,
    nutrition_dirty: bool = False,
) -> Projection:
    return Projection(
        catalog_meal_id=recipe_id,
        schema_version=schema_version,
        selection_digest=DIGEST,
        ingredient_digest=DIGEST,
        translation_digest=DIGEST,
        enrichment_digest=digest,
        query_dirty=False,
        nutrition_dirty=nutrition_dirty,
        translation_dirty=False,
        enrichment_dirty=enrichment_dirty,
        name_casefold=recipe_id,
        cuisine_casefold="vietnamese",
        search_text=recipe_id,
        browse_constraint_text="",
        selection_constraint_text="",
        browse_vegetarian=False,
        browse_no_pork=False,
        selection_contains_meat=False,
        selection_contains_pork=False,
        non_meal=False,
        total_minutes=10,
        breakfast_eligible=True,
        lunch_eligible=False,
        dinner_eligible=False,
        snack_eligible=False,
        publication_status="published",
        nutrition_status="ready",
        protein_g=1,
        carbs_g=1,
        fat_g=1,
        fiber_g=1,
        sugar_g=0,
        calories=10,
        summary_payload={},
    )


def _overlay(
    recipe_id: str,
    *,
    micros: dict,
    sources: dict | None = None,
    status: str = "ready",
    facet: str = DIGEST,
    contract: str = PREPARATION_CONTRACT_VERSION,
    suffix: str = "current",
) -> Micro:
    return Micro(
        id=_hash(f"{recipe_id}-{suffix}")[:36],
        catalog_meal_id=recipe_id,
        content_hash=_hash(f"micro-{recipe_id}-{suffix}"),
        input_facet_version=facet,
        preparation_contract_version=contract,
        micros=dict(micros),
        sources={} if sources is None else dict(sources),
        status=status,
    )


class _AsyncSession:
    """AsyncSession surface over sync SQLite. The repository only awaits execute."""

    def __init__(self, session):
        self._session = session

    async def execute(self, statement, params=None, **kwargs):
        if params is None:
            return self._session.execute(statement, **kwargs)
        return self._session.execute(statement, params, **kwargs)


class _UnitOfWork:
    def __init__(self, session):
        self._session = session
        self.catalog_preparation = CatalogPreparationReads(_AsyncSession(session))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        self._session.close()
        return False


@contextmanager
def _reads(session_factory):
    session = session_factory()
    try:
        yield CatalogPreparationReads(_AsyncSession(session))
    finally:
        session.close()


def _seed(session_factory):
    session = session_factory()
    try:
        recipes = [
            ("ready", {}),
            ("empty", {}),
            ("inactive", {"active": False}),
            ("dirty", {"enrichment_dirty": True}),
            ("nutrition-dirty", {"nutrition_dirty": True}),
            ("stale", {}),
            ("pending", {}),
            ("wrong-contract", {}),
            ("bad-schema", {"schema_version": 2}),
            (OTHER_USER_RECIPE, {}),
            ("v9", {}),
        ]
        for index in range(12):
            recipes.append((f"batch-{index}", {}))
        for recipe_id, options in recipes:
            session.add(_source(recipe_id, active=options.get("active", True)))
            session.add(
                _projection(
                    recipe_id,
                    schema_version=options.get("schema_version", 1),
                    enrichment_dirty=options.get("enrichment_dirty", False),
                    nutrition_dirty=options.get("nutrition_dirty", False),
                )
            )
        session.add(
            _overlay(
                "ready",
                micros={"iron": 2.5},
                sources={"iron": "ai_estimate"},
            )
        )
        # Older revision on the same recipe. The live projection digest does
        # not match it, so neither read may return 404.
        session.add(
            _overlay(
                "ready",
                micros={"iron": 404.0},
                sources={"iron": "ai_estimate"},
                facet=STALE_DIGEST,
                suffix="stale",
            )
        )
        session.add(_overlay("empty", micros={}, sources={}))
        session.add(_overlay("inactive", micros={"iron": 7.0}))
        session.add(_overlay("dirty", micros={"iron": 8.0}))
        session.add(_overlay("nutrition-dirty", micros={"iron": 9.0}))
        session.add(_overlay("stale", micros={"iron": 6.0}, facet=STALE_DIGEST))
        session.add(_overlay("pending", micros={"iron": 4.0}, status="pending"))
        session.add(_overlay("wrong-contract", micros={"iron": 3.0}, contract="v0"))
        session.add(_overlay("bad-schema", micros={"iron": 11.0}))
        session.add(_overlay(OTHER_USER_RECIPE, micros={"iron": OTHER_USER_IRON}))
        session.add(_overlay("v9", micros={"iron": 12.0}, contract="v9"))
        for index in range(12):
            session.add(_overlay(f"batch-{index}", micros={"iron": float(index)}))
        session.commit()
    finally:
        session.close()


@pytest.fixture
def overlay_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Source.__table__.create(engine)
    Projection.__table__.create(engine)
    Micro.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)
    _seed(session_factory)
    yield engine, session_factory
    engine.dispose()


def _iron(payload):
    if not payload:
        return None
    return payload["micros"].get("iron")


@pytest.mark.asyncio
async def test_batched_overlays_match_per_recipe_reads(overlay_db):
    """Ordering, gaps, empties, duplicates, and unrequested recipes stay aligned."""
    _engine, session_factory = overlay_db
    requested = [
        "ready",
        "missing-id",
        "empty",
        "ready",
        "inactive",
        "dirty",
        "nutrition-dirty",
        "stale",
        "pending",
        "wrong-contract",
        "bad-schema",
        "batch-0",
        "batch-11",
        "batch-0",
    ]
    hidden = (
        "missing-id",
        "inactive",
        "dirty",
        "nutrition-dirty",
        "stale",
        "pending",
        "wrong-contract",
        "bad-schema",
        OTHER_USER_RECIPE,
        "v9",
    )

    with _reads(session_factory) as reads:
        batched = await reads.get_overlays(requested)
        per_recipe = {
            recipe_id: await reads.get_overlay(recipe_id)
            for recipe_id in dict.fromkeys(requested)
        }
        versioned = await reads.get_overlays(["v9"], contract_version="v9")
        versioned_one = await reads.get_overlay("v9", contract_version="v9")

    assert batched == {
        recipe_id: payload
        for recipe_id, payload in per_recipe.items()
        if payload is not None
    }
    assert [
        batched[recipe_id] for recipe_id in ("ready", "empty", "batch-0", "batch-11")
    ] == [
        per_recipe["ready"],
        per_recipe["empty"],
        per_recipe["batch-0"],
        per_recipe["batch-11"],
    ]
    assert _iron(batched["ready"]) == 2.5
    assert batched["empty"] == {"micros": {}, "sources": {}}
    assert _iron(batched["batch-0"]) == 0.0
    assert _iron(batched["batch-11"]) == 11.0
    assert all(recipe_id not in batched for recipe_id in hidden)
    assert all(_iron(payload) != OTHER_USER_IRON for payload in batched.values())
    assert per_recipe["missing-id"] is None
    assert versioned == {"v9": versioned_one}
    assert _iron(versioned_one) == 12.0
    assert "v9" not in batched

    meals = tuple(_domain_meal(recipe_id) for recipe_id in requested)
    service = CatalogRecipeMicronutrientEnrichmentService(
        lambda: _UnitOfWork(session_factory())
    )
    batch = await service.load_cached_many(meals)
    singles = tuple([await service.load_cached(meal) for meal in meals])

    assert batch == singles
    assert [meal.id for meal in batch] == requested
    assert batch[0] == batch[3]
    assert batch[0].nutrition_micros.to_dict()["iron"] == 2.5
    assert batch[1].nutrition_micros is None
    assert batch[2].nutrition_micros is None
    assert all(
        meal.nutrition_micros is None
        or meal.nutrition_micros.to_dict().get("iron") != OTHER_USER_IRON
        for meal in batch
    )


def _select_statements(engine):
    statements = []

    def record(_connection, _cursor, statement, parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", record)
    return statements, lambda: event.remove(engine, "before_cursor_execute", record)


def _parameter_values(parameters):
    if parameters is None:
        return []
    if isinstance(parameters, dict):
        return list(parameters.values())
    return list(parameters)


@pytest.mark.asyncio
async def test_overlay_batch_query_count_is_constant(overlay_db):
    """N recipes cost one SELECT, the same as one recipe, not one SELECT per recipe."""
    engine, session_factory = overlay_db
    ids = [f"batch-{index}" for index in range(12)]
    meals = tuple(_domain_meal(recipe_id) for recipe_id in ids)
    service = CatalogRecipeMicronutrientEnrichmentService(
        lambda: _UnitOfWork(session_factory())
    )

    one, remove_one = _select_statements(engine)
    try:
        with _reads(session_factory) as reads:
            one_result = await reads.get_overlays(ids[:1])
    finally:
        remove_one()

    many, remove_many = _select_statements(engine)
    try:
        with _reads(session_factory) as reads:
            many_result = await reads.get_overlays(ids)
    finally:
        remove_many()

    empty, remove_empty = _select_statements(engine)
    try:
        with _reads(session_factory) as reads:
            empty_result = await reads.get_overlays(())
    finally:
        remove_empty()

    via_service, remove_service = _select_statements(engine)
    try:
        served = await service.load_cached_many(meals)
    finally:
        remove_service()

    assert len(one) == 1
    assert len(many) == 1
    assert len(via_service) == 1
    assert empty == []
    assert empty_result == {}
    assert len(one_result) == 1
    assert set(many_result) == set(ids)
    assert [meal.id for meal in served] == ids
    assert " IN (" in many[0][0].upper()
    assert "meal_catalog_micronutrient_enrichment" in many[0][0]
    assert all(recipe_id in _parameter_values(many[0][1]) for recipe_id in ids)
