"""Unit tests for scan correction hint loading."""

import pytest

from src.app.services.scan_correction_hints import load_correction_hints_for_scan


@pytest.mark.asyncio
async def test_load_hints_fail_open_without_user():
    hints = await load_correction_hints_for_scan(user_id=None, uow_factory=lambda: None)
    assert hints == []


@pytest.mark.asyncio
async def test_load_hints_from_meals_repo():
    class _Repo:
        async def list_recent_fix_result_raw_payloads(self, user_id, *, limit=30):
            assert user_id == "u1"
            return [
                (
                    "m1",
                    "Indian date",
                    (
                        '{"_fix_result_corrections":[{"correction":"that is grape",'
                        '"wrong_dish":"Indian date","right_dish":"Grape",'
                        '"wrong_names":["Indian date"],"right_names":["Grape"]}]}'
                    ),
                )
            ]

    hints = await load_correction_hints_for_scan(
        user_id="u1",
        user_description="grape fruit",
        meals_repo=_Repo(),
        timeout_s=1.0,
    )
    assert len(hints) == 1
    assert "Grape" in hints[0]
