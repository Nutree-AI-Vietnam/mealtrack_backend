def test_meal_image_resolved_event_import():
    from src.domain.events.meal_image_resolved_event import (
        MealImageResolvedEvent,
    )

    assert MealImageResolvedEvent is not None
