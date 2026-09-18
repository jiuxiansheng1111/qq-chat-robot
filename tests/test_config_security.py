import pytest

from app.config import Settings


def test_production_rejects_default_secrets():
    settings = Settings(
        _env_file=None,
        app_env="production",
        jwt_secret_key="change-me",
        admin_password="change-me-now",
    )
    with pytest.raises(RuntimeError):
        settings.validate_security()


def test_development_allows_defaults():
    Settings(_env_file=None, app_env="development").validate_security()


def test_secrets_are_hidden_from_settings_repr():
    secret = "must-not-appear-in-repr"
    settings = Settings(
        _env_file=None,
        jwt_secret_key=secret,
        admin_password=secret,
        onebot_access_token=secret,
        onebot_webhook_token=secret,
        llm_api_key=secret,
        groq_api_key=secret,
    )
    assert secret not in repr(settings)
