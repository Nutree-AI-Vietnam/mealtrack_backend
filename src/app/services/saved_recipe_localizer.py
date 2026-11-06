"""A saved recipe copied into a plan, in the language of the person planning.

A recipe imported while the app spoke English (or from an English source)
is saved in English. Put into the week of someone whose app speaks
Vietnamese, the plan's copy is translated: the dish name, the names the
reader sees for its ingredients, its steps and its description. Pricing
keys, amounts and macros are untouched, and the original English name is
kept as ``english_name`` so the dish keeps its photo.

Translation is a nicety, never a reason to fail a plan write: anything that
goes wrong leaves the copy as it was saved.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import replace
from typing import Any, Protocol

from src.domain.constants.languages import (
    SUPPORTED_TRANSLATION_LANGUAGES,
    normalize_language,
)
from src.domain.constants.translation_limits import iter_translation_batches
from src.domain.model.translation_result import TranslationOutcome
from src.domain.model.weekly_meal_planner import SavedRecipeSnapshot

logger = logging.getLogger(__name__)

# The plan write waits for this; past it the copy keeps its own language.
TRANSLATION_TIMEOUT_SECONDS = 8.0
_USABLE = {TranslationOutcome.TRANSLATED, TranslationOutcome.PARTIAL}
_MAX_NAME_CHARS = 255
_MAX_TEXT_CHARS = 2_000


class TextTranslator(Protocol):
    async def translate_texts(
        self, texts: Sequence[str], *, source_language: str, target_language: str
    ) -> Any: ...


async def translate_saved_recipe(
    snapshot: SavedRecipeSnapshot,
    language: str | None,
    translator: TextTranslator | None,
    *,
    timeout_seconds: float = TRANSLATION_TIMEOUT_SECONDS,
) -> SavedRecipeSnapshot:
    """The copy in ``language``; the same object when nothing changes."""
    if translator is None or not language or not snapshot.language:
        return snapshot
    source = normalize_language(snapshot.language)
    target = normalize_language(language)
    if source == target or target not in SUPPORTED_TRANSLATION_LANGUAGES:
        return snapshot
    texts = [
        snapshot.name,
        snapshot.description or "",
        *(line.shown_name for line in snapshot.ingredients),
        *(step.instruction for step in snapshot.steps),
    ]
    try:
        translated = await asyncio.wait_for(
            _translate(texts, source, target, translator), timeout_seconds
        )
    except Exception:  # noqa: BLE001 - the copy stays as it was saved
        logger.warning(
            "Saved recipe %s not translated %s->%s",
            snapshot.suggestion_id,
            source,
            target,
            exc_info=True,
        )
        return snapshot
    if translated is None:
        return snapshot
    name = translated.get(snapshot.name, "")
    description = translated.get(snapshot.description or "", "")
    return replace(
        snapshot,
        name=_clip(name, _MAX_NAME_CHARS) or snapshot.name,
        english_name=snapshot.english_name
        or (snapshot.name if source == "en" else None),
        description=(_clip(description, _MAX_TEXT_CHARS) or snapshot.description)
        if snapshot.description
        else snapshot.description,
        ingredients=tuple(
            replace(
                line,
                display_name=_clip(translated.get(line.shown_name, ""), _MAX_NAME_CHARS)
                or line.display_name,
            )
            for line in snapshot.ingredients
        ),
        steps=tuple(
            replace(
                step,
                instruction=_clip(translated.get(step.instruction, ""), _MAX_TEXT_CHARS)
                or step.instruction,
            )
            for step in snapshot.steps
        ),
        language=target,
    )


async def _translate(
    texts: list[str], source: str, target: str, translator: TextTranslator
) -> dict[str, str] | None:
    """Each distinct text's translation; None when the provider gave nothing."""
    unique = list(dict.fromkeys(text for text in texts if text.strip()))
    if not unique:
        return None
    out: dict[str, str] = {}
    for batch in iter_translation_batches(unique):
        result = await translator.translate_texts(
            batch, source_language=source, target_language=target
        )
        if getattr(result, "outcome", None) not in _USABLE:
            return None
        values = list(getattr(result, "texts", ()))
        if len(values) != len(batch):
            return None
        out.update(
            (original, value.strip())
            for original, value in zip(batch, values, strict=True)
        )
    return out


def _clip(text: str, limit: int) -> str:
    return text.strip()[:limit].strip()
