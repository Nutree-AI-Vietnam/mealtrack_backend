"""State for the meal image analysis graph."""

from typing import Any, Literal, TypedDict

MealAnalyzeScanMode = Literal["meal_scan", "food_label"]


class MealAnalyzeGraphState(TypedDict, total=False):
    """Minimal state passed through the meal analysis graph."""

    graph_version: str
    scan_mode: MealAnalyzeScanMode
    image_id: str
    content_kind: str
    image_size_bytes: int
    user_id: str
    target_date: str | None
    prepared: bool
    selected_mode: MealAnalyzeScanMode
    vision_analyzed: bool
    nutrition_parsed: bool
    reference_validated: bool
    reference_validation_pending: bool
    meal_id: str
    cache_invalidated: bool
    completed: bool
    result: Any
    error: str | None
