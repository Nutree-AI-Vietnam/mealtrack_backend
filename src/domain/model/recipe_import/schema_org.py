"""Find and flatten a schema.org ``Recipe`` in a page's JSON-LD blocks."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from src.domain.model.recipe_import.limits import (
    MAX_DESCRIPTION_CHARS,
    MAX_INGREDIENTS,
    MAX_LABEL_CHARS,
    MAX_NAME_CHARS,
    MAX_STEPS,
    MAX_STRUCTURED_LINE_CHARS,
)
from src.domain.model.recipe_import.servings import yield_servings
from src.domain.model.recipe_import.source_material import StructuredRecipe
from src.domain.model.recipe_import.text import clean_text, parse_duration_minutes

_MAX_DEPTH = 6
_MAX_NODES = 400


def find_schema_recipe(blocks: Iterable[Any]) -> StructuredRecipe | None:
    """Return the first ``Recipe`` node across parsed JSON-LD values."""
    for node in _walk(blocks):
        if _is_recipe(node):
            return _to_structured(node)
    return None


def _walk(blocks: Iterable[Any]) -> Iterator[dict[str, Any]]:
    stack: list[tuple[Any, int]] = [(block, 0) for block in reversed(list(blocks))]
    seen = 0
    while stack and seen < _MAX_NODES:
        value, depth = stack.pop()
        seen += 1
        if depth > _MAX_DEPTH:
            continue
        if isinstance(value, list):
            stack.extend((item, depth + 1) for item in reversed(value))
        elif isinstance(value, dict):
            yield value
            children = [
                value.get(key)
                for key in (
                    "@graph",
                    "mainEntity",
                    "mainEntityOfPage",
                    "itemListElement",
                    "item",
                )
                if isinstance(value.get(key), (list, dict))
            ]
            stack.extend((child, depth + 1) for child in reversed(children))


def _is_recipe(node: dict[str, Any]) -> bool:
    types = node.get("@type")
    values = types if isinstance(types, list) else [types]
    for value in values:
        if (
            isinstance(value, str)
            and value.rsplit("/", 1)[-1].rsplit(":", 1)[-1] == "Recipe"
        ):
            return True
    return False


def _to_structured(node: dict[str, Any]) -> StructuredRecipe:
    raw_yield = node.get("recipeYield")
    return StructuredRecipe(
        name=clean_text(node.get("name"), max_chars=MAX_NAME_CHARS),
        description=clean_text(
            node.get("description"), max_chars=MAX_DESCRIPTION_CHARS
        ),
        ingredients=_ingredients(node),
        instructions=_instructions(node.get("recipeInstructions")),
        recipe_yield=_yield_text(raw_yield),
        servings=yield_servings(raw_yield),
        prep_time_minutes=parse_duration_minutes(node.get("prepTime")),
        cook_time_minutes=parse_duration_minutes(node.get("cookTime")),
        total_time_minutes=parse_duration_minutes(node.get("totalTime")),
        image_url=_image_url(node.get("image")),
        author=_person_name(node.get("author")),
        cuisine=_first_text(node.get("recipeCuisine"), MAX_LABEL_CHARS),
        category=_first_text(node.get("recipeCategory"), MAX_LABEL_CHARS),
    )


def _ingredients(node: dict[str, Any]) -> tuple[str, ...]:
    raw = node.get("recipeIngredient")
    if raw is None:
        raw = node.get("ingredients")
    if isinstance(raw, str):
        raw = raw.split("\n")
    if not isinstance(raw, list):
        return ()
    lines = []
    for item in raw:
        text = clean_text(item, max_chars=MAX_STRUCTURED_LINE_CHARS)
        if text:
            lines.append(text)
        if len(lines) >= MAX_INGREDIENTS:
            break
    return tuple(lines)


def _instructions(raw: Any) -> tuple[str, ...]:
    lines: list[str] = []
    _collect_steps(raw, lines, depth=0)
    return tuple(lines[:MAX_STEPS])


def _collect_steps(raw: Any, lines: list[str], *, depth: int) -> None:
    if depth > _MAX_DEPTH or len(lines) >= MAX_STEPS:
        return
    if isinstance(raw, str):
        text = clean_text(raw, max_chars=MAX_STRUCTURED_LINE_CHARS * 8, multiline=True)
        if text:
            lines.extend(part.strip() for part in text.split("\n") if part.strip())
        return
    if isinstance(raw, list):
        for item in raw:
            _collect_steps(item, lines, depth=depth + 1)
        return
    if not isinstance(raw, dict):
        return
    children = raw.get("itemListElement")
    if children is not None:
        section = clean_text(raw.get("name"), max_chars=MAX_LABEL_CHARS)
        if section:
            lines.append(f"[{section}]")
        _collect_steps(children, lines, depth=depth + 1)
        return
    text = clean_text(
        raw.get("text") or raw.get("name") or raw.get("description"),
        max_chars=MAX_STRUCTURED_LINE_CHARS,
    )
    if text:
        lines.append(text)


def _image_url(raw: Any) -> str | None:
    if isinstance(raw, list):
        for item in raw:
            url = _image_url(item)
            if url:
                return url
        return None
    if isinstance(raw, dict):
        return _image_url(raw.get("url") or raw.get("contentUrl"))
    if isinstance(raw, str) and raw.strip().lower().startswith(("http://", "https://")):
        return raw.strip()[:2048]
    return None


def _person_name(raw: Any) -> str | None:
    if isinstance(raw, list):
        for item in raw:
            name = _person_name(item)
            if name:
                return name
        return None
    if isinstance(raw, dict):
        return clean_text(raw.get("name"), max_chars=MAX_LABEL_CHARS)
    return clean_text(raw, max_chars=MAX_LABEL_CHARS)


def _yield_text(raw: Any) -> str | None:
    """The most telling yield text: "24 cookies" rather than "24"."""
    values = raw if isinstance(raw, list) else [raw]
    texts = [
        text
        for text in (clean_text(value, max_chars=MAX_LABEL_CHARS) for value in values)
        if text
    ]
    worded = [text for text in texts if any(char.isalpha() for char in text)]
    chosen = worded or texts
    return chosen[0] if chosen else None


def _first_text(raw: Any, max_chars: int) -> str | None:
    if isinstance(raw, list):
        for item in raw:
            text = _first_text(item, max_chars)
            if text:
                return text
        return None
    return clean_text(raw, max_chars=max_chars)
