from app.config import Settings


def test_persona_examples_are_loaded(tmp_path):
    prompt_file = tmp_path / "persona.txt"
    examples_file = tmp_path / "examples.jsonl"
    prompt_file.write_text("你是测试机器人。", encoding="utf-8")
    examples_file.write_text(
        '{"messages":[{"role":"user","content":"你好"},{"role":"assistant","content":"你好呀"}]}\n',
        encoding="utf-8",
    )
    settings = Settings(
        _env_file=None,
        persona_prompt_file=str(prompt_file),
        persona_examples_file=str(examples_file),
    )
    prompt = settings.persona_prompt()
    assert "你是测试机器人" in prompt
    assert "你好呀" in prompt
