"""Web pages: schema.org first, metadata and readable text as backup."""

import httpx
import pytest

from src.domain.model.recipe_import import RecipeSourcePlatform
from src.infra.adapters.recipe_import.html_recipe_parser import (
    parse_recipe_html,
    sniff_charset,
)
from src.infra.adapters.recipe_import.safe_fetch import SafeHttpFetcher
from src.infra.adapters.recipe_import.web_page_reader import WebRecipePageReader

PAGE = """<!doctype html><html><head><title>Best Pho | Site</title>
<meta property="og:title" content="Phở bò Hà Nội">
<meta property="og:image" content="/img/pho.jpg">
<meta property="og:site_name" content="Bếp Nhà">
<meta name="description" content="Nồi phở thơm &amp; ngon">
<link rel="canonical" href="https://example.com/pho">
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Recipe","name":"Phở bò",
 "recipeIngredient":["1 kg xương bò","2 củ hành"],
 "recipeInstructions":"Ninh xương 3 giờ.\\nNướng hành.","recipeYield":"4 phần",
 "author":{"@type":"Person","name":"Cô Ba"}}
</script>
<script type="application/ld+json">{ not json </script>
<script>var tracking = "ignored";</script></head>
<body><nav>Home | About</nav><article><h1>Phở bò</h1><p>Chuyện nhà</p>
<h2>Nguyên liệu</h2><ul><li>1 kg xương bò</li><li>2 củ hành</li></ul></article>
<footer>© 2026</footer></body></html>"""


def _reader(
    body: bytes, content_type: str = "text/html; charset=utf-8"
) -> WebRecipePageReader:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": content_type}, content=body)

    async def resolver(host: str, port: int) -> list[str]:
        return ["93.184.215.14"]

    return WebRecipePageReader(
        SafeHttpFetcher(resolver=resolver, transport=httpx.MockTransport(handler))
    )


@pytest.mark.asyncio
async def test_reads_structured_recipe_metadata_and_article_text():
    material = await _reader(PAGE.encode()).read_page("https://example.com/pho?utm=x")

    assert material.platform == RecipeSourcePlatform.WEBSITE
    assert material.title == "Phở bò Hà Nội"
    assert material.description == "Nồi phở thơm & ngon"
    assert material.canonical_url == "https://example.com/pho"
    assert material.image_url == "https://example.com/img/pho.jpg"
    assert (material.site_name, material.author) == ("Bếp Nhà", "Cô Ba")
    assert material.structured_recipe is not None
    assert material.structured_recipe.servings == 4
    assert "Home" not in (material.page_text or "")
    assert "© 2026" not in (material.page_text or "")
    assert "Nguyên liệu" in (material.page_text or "")


@pytest.mark.asyncio
async def test_charset_from_the_meta_tag_when_the_header_has_none():
    body = '<html><head><meta charset="windows-1258"><title>Bánh</title></head></html>'
    material = await _reader(
        body.encode("windows-1258", errors="replace"), "text/html"
    ).read_page("https://example.com/")

    assert material.title == "Bánh"


@pytest.mark.asyncio
async def test_long_story_pages_start_near_the_ingredients():
    story = "Once upon a time. " * 2000
    body = f"<html><body><p>{story}</p><h2>Ingredients</h2><p>2 eggs</p></body></html>"

    material = await _reader(body.encode()).read_page("https://example.com/")

    assert material.page_text is not None
    assert "2 eggs" in material.page_text
    assert len(material.page_text) <= 14_001


def test_parser_skips_scripts_but_keeps_json_ld_and_meta():
    parsed = parse_recipe_html(PAGE)

    assert len(parsed.json_ld) == 1
    assert "tracking" not in parsed.text
    assert parsed.meta["og:title"] == "Phở bò Hà Nội"
    assert parsed.title == "Best Pho | Site"


def test_sniff_charset():
    assert (
        sniff_charset(
            b'<meta http-equiv="Content-Type" content="text/html; charset=EUC-JP">'
        )
        == "EUC-JP"
    )
    assert sniff_charset(b"<html>") is None
