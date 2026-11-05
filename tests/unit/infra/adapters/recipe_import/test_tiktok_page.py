"""The post a TikTok page embeds, read without opening the video."""

import json

from src.infra.adapters.recipe_import.tiktok_page import (
    TikTokPost,
    TikTokSubtitle,
    choose_tiktok_subtitle,
    is_tiktok_url,
    tiktok_post,
    without_query,
)

CAPTION = "Công thức sườn nướng cơm tấm của mẹ tui đã bán hơn 30 năm ✨ #AnCungTikTok"
SPEECH = TikTokSubtitle(
    url="https://v16m-webapp.tiktokcdn-us.com/vi/", language="vie-VN", source="ASR"
)
TRANSLATION = TikTokSubtitle(
    url="https://v16m-webapp.tiktokcdn-us.com/en/", language="eng-US", source="MT"
)


def item(**overrides):
    value = {
        "desc": CAPTION,
        "author": {"uniqueId": "lenkycungkhoa", "nickname": "Lên Ký Cùng Khoa ✨"},
        "video": {
            "duration": 78,
            "cover": "https://p19-common-sign.tiktokcdn-us.com/cover.jpeg",
            "subtitleInfos": [
                {
                    "LanguageCodeName": "eng-US",
                    "Url": TRANSLATION.url,
                    "Format": "webvtt",
                    "Source": "MT",
                },
                {
                    "LanguageCodeName": "vie-VN",
                    "Url": SPEECH.url,
                    "Format": "webvtt",
                    "Source": "ASR",
                },
            ],
        },
    }
    value.update(overrides)
    return value


def page(scope: dict) -> str:
    data = json.dumps({"__DEFAULT_SCOPE__": scope}, ensure_ascii=False)
    return (
        "<html><head><title>TikTok - Make Your Day</title></head><body>"
        '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
        f"{data}</script></body></html>"
    )


def test_the_mobile_share_page_gives_the_post():
    """The import's mobile user agent is served the "reflow" page."""
    html = page(
        {
            "webapp.app-context": {"language": "en"},
            "webapp.reflow.video.detail": {
                "statusCode": 0,
                "itemInfo": {"itemStruct": item()},
            },
        }
    )

    post = tiktok_post(html)

    assert post == TikTokPost(
        caption=CAPTION,
        author="Lên Ký Cùng Khoa ✨",
        cover_url="https://p19-common-sign.tiktokcdn-us.com/cover.jpeg",
        duration_seconds=78,
        subtitles=(TRANSLATION, SPEECH),
    )


def test_the_desktop_page_gives_the_post_too():
    html = page(
        {"webapp.video-detail": {"statusCode": 0, "itemInfo": {"itemStruct": item()}}}
    )

    post = tiktok_post(html)

    assert post is not None
    assert (post.caption, post.duration_seconds) == (CAPTION, 78)


def test_a_removed_or_private_post_is_no_post():
    html = page({"webapp.video-detail": {"statusCode": 10204, "itemInfo": {}}})

    assert tiktok_post(html) is None


def test_a_page_without_the_data_is_no_post():
    assert tiktok_post("<html><body>Please wait...</body></html>") is None
    broken = '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">{not json</script>'
    assert tiktok_post(broken) is None


def test_odd_fields_are_left_out_rather_than_trusted():
    html = page(
        {
            "webapp.video-detail": {
                "statusCode": 0,
                "itemInfo": {
                    "itemStruct": item(
                        desc="  ",
                        author={"uniqueId": "chef"},
                        video={
                            "duration": 0,
                            "cover": "http://insecure/x.jpg",
                            "subtitleInfos": [
                                {"Url": "https://cdn/sub", "Format": "creator_caption"},
                                {"Url": "javascript:alert(1)", "Format": "webvtt"},
                                "junk",
                            ],
                        },
                    )
                },
            }
        }
    )

    post = tiktok_post(html)

    assert post == TikTokPost(
        caption=None, author="chef", cover_url=None, duration_seconds=None
    )


def test_tiktok_s_transcript_of_the_speech_is_preferred_in_every_language():
    post = TikTokPost(None, None, None, None, subtitles=(TRANSLATION, SPEECH))

    assert choose_tiktok_subtitle(post, "vi") == SPEECH
    assert choose_tiktok_subtitle(post, "en") == SPEECH


def test_without_a_transcript_the_reader_s_language_comes_first():
    french = TikTokSubtitle(url="https://cdn/fr/", language="fra-FR", source="MT")
    post = TikTokPost(None, None, None, None, subtitles=(french, TRANSLATION))

    assert choose_tiktok_subtitle(post, "en-US") == TRANSLATION
    assert choose_tiktok_subtitle(post, "fr") == french
    assert choose_tiktok_subtitle(TikTokPost(None, None, None, None), "vi") is None


def test_only_tiktok_hosts_are_trusted_and_share_parameters_are_dropped():
    assert is_tiktok_url("https://www.tiktok.com/@a/video/1")
    assert is_tiktok_url("https://vt.tiktok.com/ZS1/")
    assert not is_tiktok_url("https://nottiktok.com/@a/video/1")
    assert (
        without_query("https://www.tiktok.com/@a/video/1?_r=1&_t=ZS-9")
        == "https://www.tiktok.com/@a/video/1"
    )
