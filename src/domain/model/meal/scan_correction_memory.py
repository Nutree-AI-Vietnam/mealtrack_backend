"""Personal Fix-result memory used as few-shot hints on later scans.

No model fine-tuning: store wrong→right notes on meals, retrieve recent ones
for the same user, and inject short hints into the vision user message.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9à-ỹ]{3,}", re.IGNORECASE)


@dataclass(frozen=True)
class ScanCorrectionMemory:
    """One Fix-result correction that can hint a future scan."""

    correction: str
    wrong_dish: str | None = None
    right_dish: str | None = None
    wrong_names: tuple[str, ...] = ()
    right_names: tuple[str, ...] = ()
    meal_id: str | None = None

    def hint_line(self, *, max_len: int = 140) -> str:
        wrong = self.wrong_dish or ", ".join(self.wrong_names[:3]) or "prior foods"
        right = self.right_dish or ", ".join(self.right_names[:3]) or "corrected foods"
        note = (self.correction or "").strip()
        line = f'Past correction: model said "{wrong}"; user corrected to "{right}"'
        if note:
            line = f'{line} ("{note}")'
        if len(line) > max_len:
            return line[: max_len - 1].rstrip() + "…"
        return line


def extract_fix_result_corrections(
    raw_gpt_json: str | None,
    *,
    meal_id: str | None = None,
    dish_name: str | None = None,
) -> list[ScanCorrectionMemory]:
    """Parse `_fix_result_corrections` history from a meal's raw payload."""
    try:
        raw = json.loads(raw_gpt_json or "{}")
    except (TypeError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    history = raw.get("_fix_result_corrections")
    if not isinstance(history, list):
        return []

    out: list[ScanCorrectionMemory] = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        correction = str(entry.get("correction") or "").strip()
        if not correction:
            continue
        wrong_names = _string_tuple(entry.get("wrong_names"))
        right_names = _string_tuple(entry.get("right_names"))
        wrong_dish = _optional_str(entry.get("wrong_dish")) or dish_name
        right_dish = _optional_str(entry.get("right_dish")) or dish_name
        out.append(
            ScanCorrectionMemory(
                correction=correction,
                wrong_dish=wrong_dish,
                right_dish=right_dish,
                wrong_names=wrong_names,
                right_names=right_names,
                meal_id=meal_id,
            )
        )
    return out


def rank_scan_corrections(
    memories: list[ScanCorrectionMemory],
    *,
    user_description: str | None = None,
    limit: int = 3,
) -> list[ScanCorrectionMemory]:
    """Prefer corrections that overlap the current user description; else recency."""
    if not memories or limit <= 0:
        return []

    desc_tokens = _tokens(user_description or "")
    scored: list[tuple[int, int, ScanCorrectionMemory]] = []
    for index, memory in enumerate(memories):
        # Newer history entries are later in each meal; callers should pass
        # newest meals first, so earlier list index wins on ties.
        overlap = 0
        if desc_tokens:
            haystack = _tokens(
                " ".join(
                    [
                        memory.correction,
                        memory.wrong_dish or "",
                        memory.right_dish or "",
                        " ".join(memory.wrong_names),
                        " ".join(memory.right_names),
                    ]
                )
            )
            overlap = len(desc_tokens & haystack)
        scored.append((-overlap, index, memory))

    scored.sort()
    return [item[2] for item in scored[:limit]]


def format_correction_hint_block(hints: list[str]) -> str:
    """Appendable prompt block; empty when there are no hints."""
    clean = [hint.strip() for hint in hints if hint and hint.strip()]
    if not clean:
        return ""
    lines = "\n".join(f"- {hint}" for hint in clean)
    return (
        "Past user corrections for this account — prefer when visually "
        "consistent with the image; do not invent unseen foods:\n"
        f"{lines}"
    )


def build_fix_result_history_entry(
    *,
    correction: str,
    wrong_dish: str | None,
    right_dish: str | None,
    wrong_names: list[str] | tuple[str, ...],
    right_names: list[str] | tuple[str, ...],
    edit_count_after: int,
    at_iso: str,
) -> dict[str, Any]:
    return {
        "correction": correction,
        "wrong_dish": wrong_dish,
        "right_dish": right_dish,
        "wrong_names": list(wrong_names)[:12],
        "right_names": list(right_names)[:12],
        "edit_count_after": edit_count_after,
        "at": at_iso,
    }


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            names.append(text)
    return tuple(names[:12])


def _tokens(text: str) -> set[str]:
    return {match.group(0).casefold() for match in _TOKEN_RE.finditer(text or "")}
