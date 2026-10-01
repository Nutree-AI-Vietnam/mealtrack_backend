"""Protected admin endpoints for curated meal catalog inspection."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.base_dependencies import (
    get_admin_meal_catalog_repository,
    get_catalog_image_generator_factory,
)
from src.api.dependencies.auth import require_admin_or_local
from src.api.schemas.response.admin_meal_catalog_responses import (
    AdminMealCatalogGenerateImageResponse,
    AdminMealCatalogIngredientResponse,
    AdminMealCatalogItemResponse,
    AdminMealCatalogListResponse,
)
from src.app.services.catalog_meal_image_prompt_service import (
    build_catalog_meal_image_prompt,
)

router = APIRouter(prefix="/v1/admin/meal-catalog", tags=["Admin Meal Catalog"])
MealType = Literal["breakfast", "lunch", "dinner", "snack"]


@router.get("", response_model=AdminMealCatalogListResponse)
async def list_admin_meal_catalog(
    limit: int = Query(..., ge=1, le=100),
    offset: int = Query(..., ge=0),
    q: str | None = Query(None, max_length=160),
    cuisine: str | None = Query(None, max_length=80),
    meal_type: MealType | None = Query(None),
    has_image: bool | None = Query(None),
    is_active: bool | None = Query(None),
    repository=Depends(get_admin_meal_catalog_repository),
    _admin: str = Depends(require_admin_or_local),
) -> AdminMealCatalogListResponse:
    page = await repository.list_meals(
        limit=limit,
        offset=offset,
        q=q,
        cuisine=cuisine,
        meal_type=meal_type,
        has_image=has_image,
        is_active=is_active,
    )
    return AdminMealCatalogListResponse(
        items=[_item_response(item) for item in page.items],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{catalog_id}/generate-image",
    response_model=AdminMealCatalogGenerateImageResponse,
)
async def generate_admin_meal_catalog_image(
    catalog_id: str,
    force: bool = False,
    repository=Depends(get_admin_meal_catalog_repository),
    generator_factory=Depends(get_catalog_image_generator_factory),
    _admin: str = Depends(require_admin_or_local),
) -> AdminMealCatalogGenerateImageResponse:
    row = await repository.get_meal_row(catalog_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not force and _has_image(row.image_url):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Catalog meal already has an image_url",
        )

    generator = generator_factory()
    try:
        image_url = await generator.generate_url(
            build_catalog_meal_image_prompt(row),
            quality="medium",
            size="1024x1024",
            output_format="jpeg",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Catalog meal image generation failed",
        ) from exc

    if not await repository.set_image_url(catalog_id, image_url, force=force):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Catalog meal already has an image_url",
        )
    await repository.commit()
    updated = await repository.get_meal(catalog_id)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return AdminMealCatalogGenerateImageResponse(
        item=_item_response(updated),
        image_url=image_url,
    )


def _item_response(item: Any) -> AdminMealCatalogItemResponse:
    meal = item.meal
    return AdminMealCatalogItemResponse(
        id=meal.id,
        catalog_key=meal.catalog_key,
        name=meal.name,
        cuisine=meal.cuisine,
        description=meal.description,
        image_url=meal.image_url,
        popularity_rank=meal.popularity_rank,
        meal_types=list(meal.meal_types),
        calories=meal.calories,
        protein_g=float(meal.protein_g),
        carbs_g=float(meal.carbs_g),
        fat_g=float(meal.fat_g),
        fiber_g=float(meal.fiber_g),
        ingredient_count=len(meal.ingredients),
        ingredients=[
            AdminMealCatalogIngredientResponse(
                display_name=ingredient.display_name,
                quantity=float(ingredient.quantity),
                unit=ingredient.unit,
            )
            for ingredient in meal.ingredients
        ],
        is_active=meal.is_active,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _has_image(image_url: str | None) -> bool:
    return bool((image_url or "").strip())
