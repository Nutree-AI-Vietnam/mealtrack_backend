"""Failure paths of the supplement label scan handler, with fakes."""

from types import SimpleNamespace

import pytest

from src.api.exceptions import ValidationException
from src.app.commands.supplement import ScanSupplementLabelCommand
from src.app.handlers.command_handlers.supplement_scan_handler import (
    ScanSupplementLabelCommandHandler,
)
from src.domain.exceptions.ai_exceptions import AIOutputValidationError

WHEY = {
    "is_supplement_label": True,
    "product_kind": "protein_powder",
    "product_name": "Whey",
    "form": "powder",
    "serving_grams": 30,
    "macros_per_serving": {"calories_kcal": 120, "protein_g": 24},
}


class _Uow:
    def __init__(self, meal=None):
        self.meals = SimpleNamespace(find_by_id=self._find)
        self.users = SimpleNamespace()
        self._meal = meal

    async def _find(self, meal_id, projection=None):
        return self._meal

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _handler(vision, *, meal=None, bus=None):
    async def download(url):
        return b"jpeg"

    return ScanSupplementLabelCommandHandler(
        uow_factory=lambda: _Uow(meal),
        vision_service=vision,
        download_image_bytes=download,
        event_bus=bus,
    )


def _cmd():
    return ScanSupplementLabelCommand(
        user_id="u1", image_url="https://x/img", intake_id="i1"
    )


class _Vision:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    async def analyze_with_strategy(self, image, strategy):
        if self.error:
            raise self.error
        return {"structured_data": self.result}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "vision",
    [
        _Vision(
            error=AIOutputValidationError(
                "bad", purpose="supplement_label_scan", attempt_count=1
            )
        ),
        _Vision(result={"is_supplement_label": True, "form": "lozenge"}),
        _Vision(result={"is_supplement_label": False}),
    ],
)
async def test_unreadable_replies_ask_for_a_retake(vision):
    with pytest.raises(ValidationException) as raised:
        await _handler(vision).handle(_cmd())

    assert raised.value.error_code == "SUPPLEMENT_LABEL_UNREADABLE"


@pytest.mark.asyncio
async def test_a_retry_after_the_meal_was_saved_returns_that_meal(monkeypatch):
    async def utc(*args, **kwargs):
        return "UTC"

    monkeypatch.setattr(
        "src.app.handlers.command_handlers.supplement_scan_handler."
        "resolve_user_timezone_async",
        utc,
    )
    saved = SimpleNamespace(
        meal_id="i1",
        dish_name="Whey",
        user_id="u1",
        nutrition=SimpleNamespace(
            calories=120.0, macros=SimpleNamespace(protein=24, carbs=3, fat=1)
        ),
    )

    class _NoBus:
        async def send(self, command):
            raise AssertionError("the meal must not be saved twice")

    reply = await _handler(_Vision(result=WHEY), meal=saved, bus=_NoBus()).handle(
        _cmd()
    )

    assert reply == {
        "route": "food",
        "meal": {
            "meal_id": "i1",
            "dish_name": "Whey",
            "calories": 120.0,
            "protein": 24,
            "carbs": 3,
            "fat": 1,
        },
    }
