import pytest

from app.astrbot_flash_provider import PROVIDER_TYPE
from scripts.migrate_astrbot_config import (
    MigrationError,
    _model_config,
    _validate_owned_types,
)


def test_flash_migration_keeps_native_text_compatibility():
    model = _model_config("zhipu", {
        "key": "dummy-api-key-002", "model": "glm-4-flash-250414",
        "temperature": 0.7, "max_tokens": 4096,
    })
    assert model["type"] == PROVIDER_TYPE
    _validate_owned_types({}, {model["id"]: model})


def test_compatibility_type_does_not_authorize_unrelated_provider_models():
    model = _model_config("zhipu", {
        "key": "dummy-api-key-002", "model": "glm-4.5",
        "temperature": 0.7, "max_tokens": 4096,
    })
    assert model["type"] == "openai_chat_completion"
    model["type"] = PROVIDER_TYPE
    with pytest.raises(MigrationError):
        _validate_owned_types({}, {model["id"]: model})
