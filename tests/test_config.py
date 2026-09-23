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



def test_default_persona_does_not_seed_unrelated_group_tokens():
    prompt = Settings(_env_file=None).persona_prompt()
    assert "drj" not in prompt.casefold()
    assert "hzh" not in prompt.casefold()
    assert "乐乐" not in prompt
    assert "有地将臣" in prompt
    assert "似乎有印象" not in prompt



def test_persona_encourages_topic_relevant_casual_questions():
    prompt = Settings(_env_file=None).persona_prompt()
    assert "闲聊" in prompt
    assert "自然反问一个" in prompt
    assert "网络黑话" in prompt
