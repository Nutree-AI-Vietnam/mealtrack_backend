"""Pull recipe JSON-LD, page metadata and readable text out of HTML."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

_SKIP_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "svg",
        "template",
        "iframe",
        "head",
        "nav",
        "footer",
        "aside",
        "button",
        "select",
    }
)
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "li",
        "br",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "ul",
        "ol",
        "table",
        "blockquote",
        "figcaption",
        "dd",
        "dt",
    }
)
_FOCUS_TAGS = frozenset({"article", "main"})
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)
_META_CHARSET = re.compile(
    rb"""charset\s*=\s*["']?([A-Za-z0-9_\-:.]+)""", re.IGNORECASE
)
_MAX_JSON_LD_BLOCKS = 20
_MAX_JSON_LD_CHARS = 1_000_000


@dataclass
class ParsedHtml:
    json_ld: list[Any] = field(default_factory=list)
    meta: dict[str, str] = field(default_factory=dict)
    title: str | None = None
    canonical_url: str | None = None
    text: str = ""
    focus_text: str = ""


class _RecipeHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.result = ParsedHtml()
        self._skip_depth = 0
        self._focus_depth = 0
        self._in_json_ld = False
        self._in_title = False
        self._json_buffer: list[str] = []
        self._title_buffer: list[str] = []
        self._text: list[str] = []
        self._focus: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): (value or "") for key, value in attrs}
        if tag == "meta":
            self._meta(attributes)
            return
        if tag == "link" and "canonical" in attributes.get("rel", "").lower().split():
            self.result.canonical_url = attributes.get("href") or None
            return
        if tag == "title" and self.result.title is None:
            self._in_title = True
            return
        if tag == "script" and "ld+json" in attributes.get("type", "").lower():
            self._in_json_ld = True
            self._json_buffer = []
            return
        if tag in _VOID_TAGS:
            if tag == "br":
                self._newline()
            return
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _FOCUS_TAGS:
            self._focus_depth += 1
        if tag in _BLOCK_TAGS:
            self._newline()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self._in_title = False
            self.result.title = "".join(self._title_buffer).strip() or None
            return
        if tag == "script" and self._in_json_ld:
            self._in_json_ld = False
            self._store_json_ld("".join(self._json_buffer))
            return
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _FOCUS_TAGS and self._focus_depth:
            self._focus_depth -= 1
        if tag in _BLOCK_TAGS:
            self._newline()

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self._json_buffer.append(data)
        elif self._in_title:
            self._title_buffer.append(data)
        elif not self._skip_depth and data.strip():
            self._text.append(data)
            if self._focus_depth:
                self._focus.append(data)

    def close(self) -> None:
        super().close()
        self.result.text = "".join(self._text)
        self.result.focus_text = "".join(self._focus)

    def _newline(self) -> None:
        if not self._skip_depth:
            self._text.append("\n")
            if self._focus_depth:
                self._focus.append("\n")

    def _meta(self, attributes: dict[str, str]) -> None:
        key = (attributes.get("property") or attributes.get("name") or "").lower()
        content = attributes.get("content", "").strip()
        if key and content and key not in self.result.meta:
            self.result.meta[key] = content

    def _store_json_ld(self, raw: str) -> None:
        if len(self.result.json_ld) >= _MAX_JSON_LD_BLOCKS:
            return
        text = raw.strip()
        if not text or len(text) > _MAX_JSON_LD_CHARS:
            return
        text = text.removeprefix("<!--").removesuffix("-->").strip()
        text = text.removeprefix("//<![CDATA[").removesuffix("//]]>").strip()
        try:
            self.result.json_ld.append(json.loads(text, strict=False))
        except ValueError:
            return


def parse_recipe_html(html_text: str) -> ParsedHtml:
    parser = _RecipeHtmlParser()
    try:
        parser.feed(html_text)
        parser.close()
    except (AssertionError, ValueError):
        # Badly broken markup: keep what was parsed so far.
        parser.result.text = "".join(parser._text)
        parser.result.focus_text = "".join(parser._focus)
    return parser.result


def sniff_charset(body: bytes) -> str | None:
    match = _META_CHARSET.search(body[:4096])
    return match.group(1).decode("ascii", errors="ignore") if match else None
