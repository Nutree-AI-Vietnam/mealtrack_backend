from src.domain.utils.image_compression import (
    to_compressed_cloudinary_url,
    to_compressed_image_url,
)


def test_to_compressed_cloudinary_url_injects_transformations():
    original = (
        "https://res.cloudinary.com/mealtrack/image/upload/mealtrack/user_123_meal.jpg"
    )
    transformed = to_compressed_cloudinary_url(original, max_dim=768, quality="auto")

    assert (
        transformed
        == "https://res.cloudinary.com/mealtrack/image/upload/w_768,c_limit,q_auto,f_jpg/mealtrack/user_123_meal.jpg"
    )


def test_to_compressed_cloudinary_url_preserves_already_transformed():
    already_transformed = "https://res.cloudinary.com/mealtrack/image/upload/w_768,c_limit,q_auto,f_jpg/mealtrack/user_123_meal.jpg"
    assert to_compressed_cloudinary_url(already_transformed) == already_transformed


def test_to_compressed_cloudinary_url_ignores_non_cloudinary_urls():
    s3_url = "https://s3.amazonaws.com/bucket/image.jpg"
    assert to_compressed_cloudinary_url(s3_url) == s3_url
    assert to_compressed_cloudinary_url("") == ""


def test_to_compressed_image_url_cloudflare_default():
    url = "https://imagedelivery.net/PeQb0oPRIbwHNu4iebuEpQ/img-123/public"
    transformed = to_compressed_image_url(url, max_dim=768)
    assert (
        transformed
        == "https://imagedelivery.net/PeQb0oPRIbwHNu4iebuEpQ/img-123/w=768,fit=scale-down,f=auto"
    )


def test_to_compressed_image_url_cloudflare_custom_domain():
    url = "https://media.nutree.ai/img-123/public"
    transformed = to_compressed_image_url(
        url, max_dim=768, custom_domain="media.nutree.ai"
    )
    assert transformed == "https://media.nutree.ai/img-123/w=768,fit=scale-down,f=auto"

    # Also works when custom_domain is passed with https:// and trailing slash
    transformed_with_scheme = to_compressed_image_url(
        url, max_dim=768, custom_domain="https://media.nutree.ai/"
    )
    assert (
        transformed_with_scheme
        == "https://media.nutree.ai/img-123/w=768,fit=scale-down,f=auto"
    )


def test_to_compressed_image_url_cloudflare_malformed_url_preserved():
    # URL missing variant segment — must NOT strip or replace the image ID
    malformed = "https://imagedelivery.net/PeQb0oPRIbwHNu4iebuEpQ/img-123"
    assert to_compressed_image_url(malformed) == malformed

    malformed_custom = "https://media.nutree.ai/img-123"
    assert (
        to_compressed_image_url(malformed_custom, custom_domain="media.nutree.ai")
        == malformed_custom
    )


def test_to_compressed_image_url_cloudflare_preserves_already_transformed():
    already = "https://imagedelivery.net/PeQb0oPRIbwHNu4iebuEpQ/img-123/w=768,fit=scale-down,f=auto"
    assert to_compressed_image_url(already) == already


def test_to_compressed_image_url_anti_spoofing_ignores_arbitrary_domains():
    spoofed_cf = "https://evil.com/imagedelivery.net/hash/img-123/public"
    assert to_compressed_image_url(spoofed_cf) == spoofed_cf

    spoofed_cld = "https://evil.com/res.cloudinary.com/image/upload/img.jpg"
    assert to_compressed_image_url(spoofed_cld) == spoofed_cld


def test_to_compressed_image_url_cloudflare_flexible_variants_disabled():
    url = "https://imagedelivery.net/PeQb0oPRIbwHNu4iebuEpQ/img-123/public"
    # When flexible_variants_enabled is False, preserves original URL to prevent 404
    assert to_compressed_image_url(url, flexible_variants_enabled=False) == url

    custom_url = "https://media.nutree.ai/img-123/public"
    assert (
        to_compressed_image_url(
            custom_url,
            custom_domain="media.nutree.ai",
            flexible_variants_enabled=False,
        )
        == custom_url
    )

