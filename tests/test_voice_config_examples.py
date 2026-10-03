from pathlib import Path


def test_voice_weight_routes_are_disabled_in_the_copyable_env_example():
    env_example = Path(".env.example").read_text(encoding="utf-8")

    assert "VOICE_SOVITS_WEIGHTS_BY_LANGUAGE_JSON=\n" in env_example
    assert "VOICE_SOVITS_WEIGHTS_BY_PROFILE_JSON=\n" in env_example
    assert "此处尚未部署 e13" in env_example
    assert "C:/models/" not in env_example
