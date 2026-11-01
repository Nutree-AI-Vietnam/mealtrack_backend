"""Links a user imports from: found in share text, refused when unsafe, classified."""

import pytest

from src.domain.exceptions.recipe_import_exceptions import (
    RecipeImportError,
    RecipeImportFailure,
)
from src.domain.model.recipe_import.enums import RecipeSourcePlatform
from src.domain.model.recipe_import.link_policy import (
    classify_recipe_url,
    extract_recipe_url,
    normalize_recipe_url,
    youtube_video_id,
)


class TestExtract:
    def test_finds_the_link_inside_share_sheet_text(self):
        url = extract_recipe_url(
            "Xem món này nè! https://vt.tiktok.com/ZSabc123/ ngon lắm."
        )

        assert url == "https://vt.tiktok.com/ZSabc123/"

    def test_adds_https_to_a_bare_domain(self):
        assert extract_recipe_url("www.allrecipes.com/recipe/1") == (
            "https://www.allrecipes.com/recipe/1"
        )

    def test_drops_the_fragment_and_keeps_the_query(self):
        assert extract_recipe_url("https://example.com/pho?x=1#comments") == (
            "https://example.com/pho?x=1"
        )

    @pytest.mark.parametrize("text", ["", "no link here", "just words and spaces"])
    def test_text_without_a_link_is_an_invalid_source(self, text):
        with pytest.raises(RecipeImportError) as caught:
            extract_recipe_url(text)

        assert caught.value.failure == RecipeImportFailure.INVALID_SOURCE


class TestNormalize:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/admin",
            "http://10.0.0.8/",
            "http://[::1]/",
            "http://localhost:8000/",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://printer.local/",
            "http://intranet/",
            "http://2130706433/",
            "http://0x7f.1/",
            "ftp://example.com/recipe",
            "file:///etc/passwd",
            "http://user:secret@example.com/",
            "http://example.com:22/",
        ],
    )
    def test_refuses_links_the_server_must_not_fetch(self, url):
        with pytest.raises(RecipeImportError) as caught:
            normalize_recipe_url(url)

        assert caught.value.failure == RecipeImportFailure.URL_NOT_ALLOWED

    def test_encodes_international_host_names(self):
        assert normalize_recipe_url("https://bếp.vn/pho").startswith("https://xn--")

    def test_allows_standard_ports(self):
        assert (
            normalize_recipe_url("https://example.com:443/a")
            == "https://example.com:443/a"
        )

    def test_refuses_overlong_links(self):
        with pytest.raises(RecipeImportError) as caught:
            normalize_recipe_url("https://example.com/" + "a" * 3000)

        assert caught.value.failure == RecipeImportFailure.INVALID_SOURCE


class TestClassify:
    @pytest.mark.parametrize(
        ("url", "platform"),
        [
            ("https://www.tiktok.com/@chef/video/123", RecipeSourcePlatform.TIKTOK),
            ("https://vm.tiktok.com/ZS1/", RecipeSourcePlatform.TIKTOK),
            ("https://www.instagram.com/reel/C1/", RecipeSourcePlatform.INSTAGRAM),
            ("https://www.facebook.com/reel/1", RecipeSourcePlatform.FACEBOOK),
            ("https://fb.watch/abc/", RecipeSourcePlatform.FACEBOOK),
            ("https://www.allrecipes.com/recipe/1", RecipeSourcePlatform.WEBSITE),
            ("https://nottiktok.com/x", RecipeSourcePlatform.WEBSITE),
        ],
    )
    def test_platform_by_host(self, url, platform):
        assert classify_recipe_url(url) == (platform, url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://youtu.be/dQw4w9WgXcQ?si=share",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=12",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
            "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
        ],
    )
    def test_youtube_videos_become_the_canonical_watch_link(self, url):
        assert classify_recipe_url(url) == (
            RecipeSourcePlatform.YOUTUBE,
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )

    def test_youtube_pages_without_a_video_are_web_pages(self):
        url = "https://www.youtube.com/@somechef"

        assert classify_recipe_url(url) == (RecipeSourcePlatform.WEBSITE, url)
        assert youtube_video_id(url) is None
