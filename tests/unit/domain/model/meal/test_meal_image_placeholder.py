from uuid import uuid4

import pytest

from src.domain.model.meal import MealImage


@pytest.mark.parametrize(
    ("size_bytes", "url", "expected"),
    [
        (1, None, True),
        (1, "", True),
        (1, "https://cdn.example.com/catalog/pho.jpg", False),
        (48_213, None, False),
        (48_213, "https://imagedelivery.net/h/mealtrack/x/public", False),
    ],
)
def test_is_placeholder_only_for_photo_less_rows(size_bytes, url, expected):
    image = MealImage(
        image_id=str(uuid4()), format="jpeg", size_bytes=size_bytes, url=url
    )

    assert image.is_placeholder is expected
