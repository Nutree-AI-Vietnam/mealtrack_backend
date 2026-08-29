from src.domain.utils.image_compression import (
    to_compressed_cloudinary_url,
)


def test_to_compressed_cloudinary_url_injects_transformations():
    original = "https://res.cloudinary.com/mealtrack/image/upload/mealtrack/user_123_meal.jpg"
    transformed = to_compressed_cloudinary_url(original, max_dim=768, quality="auto")

    assert (
        transformed
        == "https://res.cloudinary.com/mealtrack/image/upload/w_768,c_limit,q_auto,f_jpg/mealtrack/user_123_meal.jpg"
    )


def test_to_compressed_cloudinary_url_preserves_already_transformed():
    already_transformed = "https://res.cloudinary.com/mealtrack/image/upload/w_768,c_limit,q_auto,f_jpg/mealtrack/user_123_meal.jpg"
    assert (
        to_compressed_cloudinary_url(already_transformed)
        == already_transformed
    )


def test_to_compressed_cloudinary_url_ignores_non_cloudinary_urls():
    s3_url = "https://s3.amazonaws.com/bucket/image.jpg"
    assert to_compressed_cloudinary_url(s3_url) == s3_url
    assert to_compressed_cloudinary_url("") == ""
