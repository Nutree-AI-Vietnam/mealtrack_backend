from src.infra.config.settings import Settings


def test_cloudflare_custom_domain_normalizes_scheme_and_slashes():
    s1 = Settings(
        CLOUDFLARE_CUSTOM_DOMAIN="https://images.example.com/", _env_file=None
    )
    assert s1.CLOUDFLARE_CUSTOM_DOMAIN == "images.example.com"

    s2 = Settings(
        CLOUDFLARE_CUSTOM_DOMAIN="http://media.nutree.ai/some/path", _env_file=None
    )
    assert s2.CLOUDFLARE_CUSTOM_DOMAIN == "media.nutree.ai"

    s3 = Settings(CLOUDFLARE_CUSTOM_DOMAIN="  MEDIA.Nutree.AI  ", _env_file=None)
    assert s3.CLOUDFLARE_CUSTOM_DOMAIN == "media.nutree.ai"


def test_cloudflare_custom_domain_defaults_when_empty():
    s = Settings(CLOUDFLARE_CUSTOM_DOMAIN="", _env_file=None)
    assert s.CLOUDFLARE_CUSTOM_DOMAIN == ""
