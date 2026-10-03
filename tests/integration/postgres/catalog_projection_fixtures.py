"""Real PostgreSQL parity and fencing for rebuildable catalog projections."""

import hashlib
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from src.infra.database.models.food_reference_model import FoodReferenceModel
from src.infra.database.models.meal_recommendation import (
    AllergenReferenceORM,
    MealCatalogAllergenORM,
    MealCatalogIngredientORM,
    MealCatalogORM,
)
from src.infra.repositories.catalog_projection_rebuilder import (
    CatalogProjectionRebuilder,
)


async def seed_catalog(session):
    food = FoodReferenceModel(
        name="Rice",
        source="catalog_seed",
        is_verified=True,
        protein_100g=3,
        carbs_100g=28,
        fat_100g=1,
        fiber_100g=2,
        sugar_100g=0,
        extra_nutrients={"iron_mg": 1},
        density=1,
    )
    known = []
    for code in ("milk", "peanut"):
        allergen = (
            await session.execute(
                select(AllergenReferenceORM).where(AllergenReferenceORM.code == code)
            )
        ).scalar_one_or_none()
        if allergen is None:
            allergen = AllergenReferenceORM(id=str(uuid4()), code=code, name=code)
            session.add(allergen)
        known.append(allergen)
    session.add(food)
    await session.flush()
    recipes = []
    for i, (name, ingredient, rank, minutes, allergies) in enumerate(
        [
            ("Straße Tofu", "Tofu", 0, None, [known[0]]),
            ("STRASSE Tofu", "Cá", 0, 10, [known[1]]),
            ("İstanbul bowl", "Rice", 1, 30, []),
            ("Σίσυφος Bowl", "Pork", None, 35, [known[1]]),
            ("100%_Rice", "Rice", 2, 20, [known[1]]),
            ("Tofu pudding", "Tofu", 3, 5, [known[1]]),
        ]
    ):
        key = str(uuid4())
        recipe = MealCatalogORM(
            id=key,
            catalog_key=key,
            content_hash=hashlib.sha256(key.encode()).hexdigest(),
            name=name,
            cuisine="Straße",
            description="Fresh sesame",
            popularity_rank=rank,
            prep_time_minutes=minutes,
            cook_time_minutes=None,
            lunch_eligible=True,
            dinner_eligible=i % 2 == 0,
            is_active=True,
            recipe_payload={},
            payload_digest="a" * 64,
            publication_status="published",
            nutrition_status="ready",
            ingredients=[
                MealCatalogIngredientORM(
                    position=1,
                    food_reference_id=food.id,
                    display_name=ingredient,
                    quantity=Decimal("100"),
                    unit="g",
                    category="pantry",
                )
            ],
            allergen_links=[
                MealCatalogAllergenORM(allergen_id=allergen.id, source="explicit")
                for allergen in allergies
            ],
        )
        recipes.append(recipe)
    session.add_all(recipes)
    await session.flush()
    await CatalogProjectionRebuilder(session).rebuild([recipe.id for recipe in recipes])
    await session.commit()
    return [recipe.id for recipe in recipes], food.id
