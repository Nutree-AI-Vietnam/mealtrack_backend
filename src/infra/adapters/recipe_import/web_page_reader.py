"""Read a recipe web page: schema.org data first, readable text as backup."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from src.domain.model.recipe_import.enums import RecipeSourcePlatform
from src.domain.model.recipe_import.limits import (
    MAX_CAPTION_CHARS,
    MAX_LABEL_CHARS,
    MAX_NAME_CHARS,
    MAX_PAGE_TEXT_CHARS,
)
from src.domain.model.recipe_import.schema_org import find_schema_recipe
from src.domain.model.recipe_import.source_material import RecipeSourceMaterial
from src.domain.model.recipe_import.text import clean_text
from src.infra.adapters.recipe_import.html_recipe_parser import (
    parse_recipe_html,
    sniff_charset,
)
from src.infra.adapters.recipe_import.safe_fetch import SafeHttpFetcher

MAX_HTML_BYTES = 3 * 1024 * 1024
HTML_TYPES = ("text/html", "application/xhtml+xml")
HTML_ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"
# With complete structured data the page text is only a cross-check.
_STRUCTURED_PAGE_TEXT_CHARS = 3_000
_FOCUS_MIN_CHARS = 400
_RECIPE_HEADINGS = re.compile(
    r"\b(ingredients?|nguyên liệu|thành phần|ingredientes|ingrédients|zutaten|材料|食材|用料)\b",
    re.IGNORECASE,
)


class WebRecipePageReader:
    """``RecipeWebPagePort`` over the SSRF-safe fetcher."""

    def __init__(self, fetcher: SafeHttpFetcher) -> None:
        self._fetcher = fetcher

    async def read_page(self, url: str) -> RecipeSourceMaterial:
        return await self.read(url, platform=RecipeSourcePlatform.WEBSITE)

    async def read(
        self, url: str, *, platform: RecipeSourcePlatform
    ) -> RecipeSourceMaterial:
        resource = await self._fetcher.get(
            url,
            max_bytes=MAX_HTML_BYTES,
            accept=HTML_ACCEPT,
            allowed_types=HTML_TYPES,
            truncate_oversized=True,
        )
        charset = resource.charset or sniff_charset(resource.body)
        encoding = charset if charset and _known(charset) else "utf-8"
        html_text = resource.body.decode(encoding, errors="replace")
        parsed = parse_recipe_html(html_text)
        meta = parsed.meta
        structured = find_schema_recipe(parsed.json_ld)
        text_limit = (
            _STRUCTURED_PAGE_TEXT_CHARS
            if structured is not None and structured.is_complete
            else MAX_PAGE_TEXT_CHARS
        )
        image = (
            (structured.image_url if structured else None)
            or meta.get("og:image")
            or meta.get("og:image:url")
            or meta.get("twitter:image")
        )
        canonical = parsed.canonical_url or meta.get("og:url")
        return RecipeSourceMaterial(
            platform=platform,
            source_url=url,
            canonical_url=_absolute(canonical, resource.url) or resource.url,
            title=clean_text(
                meta.get("og:title") or meta.get("twitter:title") or parsed.title,
                max_chars=MAX_NAME_CHARS,
            ),
            description=clean_text(
                meta.get("og:description")
                or meta.get("twitter:description")
                or meta.get("description"),
                max_chars=MAX_CAPTION_CHARS,
                multiline=True,
            ),
            author=clean_text(
                (structured.author if structured else None)
                or meta.get("author")
                or meta.get("article:author"),
                max_chars=MAX_LABEL_CHARS,
            ),
            site_name=clean_text(meta.get("og:site_name"), max_chars=MAX_LABEL_CHARS),
            image_url=_absolute(image, resource.url),
            structured_recipe=structured,
            page_text=_page_text(parsed.text, parsed.focus_text, text_limit),
        )


def _page_text(text: str, focus_text: str, limit: int) -> str | None:
    focus = clean_text(focus_text, max_chars=10**7, multiline=True) or ""
    full = clean_text(text, max_chars=10**7, multiline=True) or ""
    chosen = focus if len(focus) >= _FOCUS_MIN_CHARS else full
    if len(chosen) <= limit:
        return chosen or None
    # Long blog posts put the recipe card late; start near the first
    # ingredients heading instead of at the top of the story.
    match = _RECIPE_HEADINGS.search(chosen)
    start = max(0, match.start() - 1_500) if match else 0
    return clean_text(chosen[start : start + limit], max_chars=limit, multiline=True)


def _absolute(value: str | None, base: str) -> str | None:
    if not value:
        return None
    joined = urljoin(base, value.strip())
    if not joined.lower().startswith(("http://", "https://")) or len(joined) > 2048:
        return None
    return joined


def _known(charset: str) -> bool:
    try:
        "".encode(charset)
    except LookupError:
        return False
    return True
