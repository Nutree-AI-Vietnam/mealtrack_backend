from src.infra.config.settings import Settings


def test_provider_rpm_defaults_when_unset():
    settings = Settings(_env_file=None)
    assert settings.NUTRITION_PROVIDER_GLOBAL_RPM == 60


def test_provider_rpm_defaults_when_empty_string():
    settings = Settings(NUTRITION_PROVIDER_GLOBAL_RPM="", _env_file=None)
    assert settings.NUTRITION_PROVIDER_GLOBAL_RPM == 60
