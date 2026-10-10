"""
Meals API endpoints using event-driven architecture.

Thin router — route bodies live in sibling modules by flow group.
"""

from fastapi import APIRouter

from src.api.routes.v1.meals_analyze import MAX_FILE_SIZE
from src.api.routes.v1.meals_analyze import router as analyze_router
from src.api.routes.v1.meals_edit import router as edit_router
from src.api.routes.v1.meals_manual_text import router as manual_text_router
from src.api.routes.v1.meals_read import get_meal_value_insights
from src.api.routes.v1.meals_read import router as read_router
from src.api.routes.v1.meals_recent_favorites import (
    router as recent_favorites_router,
)

router = APIRouter(prefix="/v1/meals", tags=["Meals"])
router.include_router(recent_favorites_router)
router.include_router(analyze_router)
router.include_router(manual_text_router)
router.include_router(read_router)
router.include_router(edit_router)

__all__ = ["router", "MAX_FILE_SIZE", "get_meal_value_insights"]
