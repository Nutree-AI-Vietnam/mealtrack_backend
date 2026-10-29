"""Unit tests for personal Fix-result scan memory helpers."""

import json

from src.domain.model.meal.scan_correction_memory import (
    ScanCorrectionMemory,
    extract_fix_result_corrections,
    format_correction_hint_block,
    rank_scan_corrections,
)


def test_extract_and_rank_prefers_overlapping_correction():
    raw = json.dumps(
        {
            "_fix_result_corrections": [
                {
                    "correction": "it was fish not chicken",
                    "wrong_dish": "Chicken bowl",
                    "right_dish": "Fish bowl",
                    "wrong_names": ["Chicken", "Rice"],
                    "right_names": ["Fish", "Rice"],
                },
                {
                    "correction": "no cheese",
                    "wrong_dish": "Salad",
                    "right_dish": "Salad",
                    "wrong_names": ["Salad", "Cheese"],
                    "right_names": ["Salad"],
                },
            ]
        }
    )
    memories = extract_fix_result_corrections(raw, meal_id="m1")
    ranked = rank_scan_corrections(memories, user_description="fish rice", limit=1)
    assert len(ranked) == 1
    assert "Fish" in ranked[0].right_dish or "fish" in ranked[0].correction


def test_hint_block_and_line_include_wrong_to_right():
    memory = ScanCorrectionMemory(
        correction="that is grape not indian date",
        wrong_dish="Indian date",
        right_dish="Grape",
        wrong_names=("Indian date",),
        right_names=("Grape",),
    )
    line = memory.hint_line()
    assert "Indian date" in line
    assert "Grape" in line
    block = format_correction_hint_block([line])
    assert "Past user corrections" in block
    assert "Grape" in block


def test_format_empty_hints_is_blank():
    assert format_correction_hint_block([]) == ""
