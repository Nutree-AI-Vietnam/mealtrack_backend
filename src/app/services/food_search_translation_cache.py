"""Remember recent per-text translations so repeated food names skip the provider.

Search pages repeat the same names ("Chicken breast", "Ức gà") across queries
and users, and each translator round trip costs hundreds of milliseconds. Only
texts missing from the cache are sent to the wrapped translator.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import Protocol

from src.domain.constants.languages import normalize_language
from src.domain.model.translation_result import TranslationOutcome, TranslationResult

_Key = tuple[str, str, str]
# Queries are user input; long ones are translated but not kept, so the cache
# stays small no matter what is typed.
_MAX_CACHED_TEXT_LENGTH = 256


class _TextTranslator(Protocol):
    async def translate_texts(
        self,
        texts: Sequence[str],
        source_language: str | None,
        target_language: str | None,
    ) -> TranslationResult: ...


class CachedTextTranslation:
    """Per-text LRU with a TTL in front of a translator.

    Only complete (TRANSLATED) answers are remembered, so a partial or failed
    batch is retried on the next search instead of pinning the fallback text.
    """

    def __init__(
        self,
        inner: _TextTranslator,
        *,
        max_entries: int = 4096,
        ttl_seconds: float = 6 * 60 * 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: OrderedDict[_Key, tuple[float, str]] = OrderedDict()

    async def translate_texts(
        self,
        texts: Sequence[str],
        source_language: str | None,
        target_language: str | None,
    ) -> TranslationResult:
        original = tuple(str(text) for text in texts)
        source = normalize_language(source_language)
        target = normalize_language(target_language)
        unique = list(dict.fromkeys(text for text in original if text))
        if not unique or source == target:
            return await self._inner.translate_texts(
                list(original), source_language, target_language
            )

        translated: dict[str, str] = {}
        misses: list[str] = []
        now = self._clock()
        for text in unique:
            cached = self._get((source, target, text), now)
            if cached is None:
                misses.append(text)
            else:
                translated[text] = cached

        outcome = TranslationOutcome.TRANSLATED
        if misses:
            result = await self._inner.translate_texts(misses, source, target)
            outcome = self._fold_misses(misses, result, translated)
            if outcome is TranslationOutcome.TRANSLATED:
                stored_at = self._clock()
                for text in misses:
                    if len(text) <= _MAX_CACHED_TEXT_LENGTH:
                        self._put((source, target, text), translated[text], stored_at)

        return TranslationResult(
            tuple(translated.get(text, text) if text else text for text in original),
            outcome,
            source,
            target,
        )

    @staticmethod
    def _fold_misses(
        misses: list[str], result: TranslationResult, translated: dict[str, str]
    ) -> TranslationOutcome:
        """Add the translator's answers for ``misses`` to the cache hits."""
        had_hits = bool(translated)
        values = tuple(result.texts)
        for text, value in zip(misses, values, strict=False):
            if value:
                translated[text] = value
        complete = len(values) == len(misses) and all(values)
        if result.outcome is TranslationOutcome.TRANSLATED and complete:
            return TranslationOutcome.TRANSLATED
        if had_hits or result.outcome in (
            TranslationOutcome.TRANSLATED,
            TranslationOutcome.PARTIAL,
        ):
            return TranslationOutcome.PARTIAL
        return result.outcome

    def _get(self, key: _Key, now: float) -> str | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, value = entry
        if now - stored_at >= self._ttl_seconds:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return value

    def _put(self, key: _Key, value: str, now: float) -> None:
        self._entries[key] = (now, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
