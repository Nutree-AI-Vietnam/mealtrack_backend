import pytest

from src.api.exceptions import ValidationException
from src.api.routes.v1.meals_route_helpers import validate_uploaded_image_url


def test_validate_uploaded_image_url_with_custom_domain_containing_scheme(monkeypatch):
    import src.infra.config.settings as settings_module

    class _SettingsWithScheme:
        CLOUDFLARE_CUSTOM_DOMAIN = "https://images.example.com/"

    monkeypatch.setattr(settings_module, "get_settings", lambda: _SettingsWithScheme())

    # Valid image URL from custom domain should pass
    validate_uploaded_image_url("https://images.example.com/img-123/public", "img-123")

    # Invalid host should still fail
    with pytest.raises(ValidationException, match="authorized image host URL"):
        validate_uploaded_image_url("https://evil.com/img-123/public", "img-123")
