import base64
import copy
import io

import pytest
from PIL import Image

from app.astrbot_vision_provider import readable_image_parts
from scripts.configure_qq_vision import planned_configs


@pytest.mark.parametrize("size", [(2160, 7890), (7890, 2160)])
def test_long_report_has_readable_complete_detail_coverage(size):
    image = Image.new("RGB", size, "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    parts = readable_image_parts(buffer.getvalue())
    assert len(parts) >= 4 and "100%" in parts[-1][1]
    for data, label in parts[1:-1]:
        with Image.open(io.BytesIO(base64.b64decode(data.split(",", 1)[1]))) as crop:
            assert min(crop.size) == 1152
            assert max(crop.size) <= 1440


def test_ordinary_picture_needs_only_one_image():
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), "blue").save(buffer, format="PNG")
    assert len(readable_image_parts(buffer.getvalue())) == 1


def test_vision_config_preserves_existing_models_and_other_platform_defaults():
    base = {
        "provider": [
            {
                "id": "qqchat-zhipu-vision",
                "model": "glm-4.6v-flash",
                "type": "openai_chat_completion",
                "provider_source_id": "existing",
            },
            {
                "id": "qqchat-zhipu-vision-backup",
                "model": "glm-4.1v-thinking-flash",
                "provider_source_id": "existing",
            },
            {"id": "qqchat-chatgpt-luna", "model": "gpt-6-luna"},
        ],
        "provider_settings": {"default_image_caption_provider_id": "qqchat-zhipu-vision"},
        "agent_runner": {"chat_model": "qqchat-chatgpt-luna"},
        "bili_setting": "preserved",
    }
    original = copy.deepcopy(base)
    global_config, qq = planned_configs(base, base)
    assert base == original
    assert global_config["provider"][:-1] == base["provider"]
    assert {k: v for k, v in global_config.items() if k != "provider"} == {
        k: v for k, v in base.items() if k != "provider"
    }
    assert qq["provider_settings"]["default_image_caption_provider_id"] == "qqchat-qq-vision"
    assert qq["agent_runner"] == base["agent_runner"]
    assert planned_configs(global_config, qq) == (global_config, qq)
