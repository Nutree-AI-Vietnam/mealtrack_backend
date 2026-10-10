"""
Foods API routes: manual search/autocomplete, details, and barcode lookup.

Uses a lightweight singleton event bus to avoid re-initializing
heavy services (Cloudinary, AI providers, etc.) on every request.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_food_search_event_bus
from src.api.middleware.accept_language import get_request_language
from src.api.middleware.rate_limit import limiter
from src.api.schemas.response.barcode_product_response import BarcodeProductResponse
from src.app.queries.food.get_food_details_query import GetFoodDetailsQuery
from src.app.queries.food.get_provider_food_details_query import (
    GetProviderFoodDetailsQuery,
)
from src.app.queries.food.lookup_barcode_query import LookupBarcodeQuery
from src.app.queries.food.search_foods_query import SearchFoodsQuery
from src.domain.exceptions.barcode_exceptions import InvalidBarcodeError
from src.domain.services.barcode import normalize_gtin

router = APIRouter(prefix="/v1/foods", tags=["Foods"])

FOOD_SEARCH_LIMIT = "30/minute"
FOOD_AUTOCOMPLETE_LIMIT = "60/minute"
FOOD_DETAILS_LIMIT = "30/minute"
FOOD_PROVIDER_DETAILS_LIMIT = "60/minute"
FOOD_BARCODE_LIMIT = "20/minute"
FOOD_POPULAR_STAPLES_LIMIT = "60/minute"


@router.get("/popular-staples")
@limiter.limit(FOOD_POPULAR_STAPLES_LIMIT)
async def get_popular_staples(
    request: Request,
    _: str = Depends(get_current_user_id),
):
    """Curated staple foods from food_reference (no live FatSecret search)."""
    from src.app.queries.food.get_popular_staples_query import GetPopularStaplesQuery

    event_bus = get_food_search_event_bus()
    language = get_request_language(request)
    return await event_bus.send(GetPopularStaplesQuery(language=language))


@router.get("/search")
@limiter.limit(FOOD_SEARCH_LIMIT)
async def search_foods(
    request: Request,
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=50),
    _: str = Depends(get_current_user_id),
):
    """Search foods for manual logging using the lightweight event bus."""
    event_bus = get_food_search_event_bus()
    language = get_request_language(request)
    query = SearchFoodsQuery(query=q, limit=limit, language=language)
    return await event_bus.send(query)


@router.get("/autocomplete")
@limiter.limit(FOOD_AUTOCOMPLETE_LIMIT)
async def autocomplete_foods(
    request: Request,
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=20),
    _: str = Depends(get_current_user_id),
):
    """Autocomplete foods for manual logging using the search provider path."""
    event_bus = get_food_search_event_bus()
    language = get_request_language(request)
    query = SearchFoodsQuery(
        query=q,
        limit=limit,
        language=language,
        autocomplete=True,
    )
    return await event_bus.send(query)


@router.get("/provider/{namespace}/{source_food_id}/details")
@limiter.limit(FOOD_PROVIDER_DETAILS_LIMIT)
async def get_provider_food_details(
    request: Request,
    namespace: str,
    source_food_id: str,
    _: str = Depends(get_current_user_id),
):
    """Resolve one provider food (FatSecret) after search select — single food.get."""
    event_bus = get_food_search_event_bus()
    language = get_request_language(request)
    try:
        return await event_bus.send(
            GetProviderFoodDetailsQuery(
                source_namespace=namespace,
                source_food_id=source_food_id,
                language=language,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{fdc_id}/details")
@limiter.limit(FOOD_DETAILS_LIMIT)
async def get_food_details(
    request: Request,
    fdc_id: int,
    _: str = Depends(get_current_user_id),
):
    """Get food details using lightweight singleton event bus."""
    event_bus = get_food_search_event_bus()
    query = GetFoodDetailsQuery(fdc_id=fdc_id)
    return await event_bus.send(query)


@router.get("/barcode/{barcode}", response_model=BarcodeProductResponse)
@limiter.limit(FOOD_BARCODE_LIMIT)
async def lookup_barcode(
    request: Request,
    barcode: str,
    user_id: str = Depends(get_current_user_id),
):
    """Look up product by barcode from structured providers and estimates."""
    event_bus = get_food_search_event_bus()
    language = get_request_language(request)
    try:
        lookup_keys = normalize_gtin(barcode)
    except InvalidBarcodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    query = LookupBarcodeQuery(
        barcode=lookup_keys.gtin_14,
        language=language,
        scanned_barcode=lookup_keys.raw,
        aliases=lookup_keys.aliases,
    )
    result = await event_bus.send(query)

    if result is None:
        raise HTTPException(status_code=404, detail="Product not found")

    return BarcodeProductResponse(**result)
