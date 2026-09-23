import pytest

from app.services.affection import (
    AFFECTION_INITIAL,
    MEMORY_UNLOCK_SCORE,
    AffectionAssessment,
    affection_change_text,
    affection_prompt,
    affection_stage,
    affection_status_text,
    assess_affection,
    rule_based_affection,
)


def test_affection_defaults_and_unlock_threshold():
    assert AFFECTION_INITIAL == 40
    assert MEMORY_UNLOCK_SCORE == 55
    assert "40/100" in affection_status_text(40)
    assert "55" in affection_status_text(40)


def test_affection_stage_changes_reply_behavior():
    assert affection_stage(5)[0] == "冰封"
    assert affection_stage(30)[0] == "高冷傲娇"
    assert affection_stage(65)[0] == "亲近"
    assert affection_stage(90)[0] == "十分亲密"
    assert "2到8个汉字" in affection_prompt(5)
    assert "1到2句" in affection_prompt(30)
    assert "主动接一个自然的小问题" in affection_prompt(65)
    assert "排他" in affection_prompt(90)


def test_rule_based_affection_caps_positive_and_negative():
    positive = rule_based_affection("你刚才回答得太好了，完美！", "上一轮回答")
    assert positive.delta == 5

    moderate = rule_based_affection("你真垃圾", "上一轮回答")
    assert moderate.delta == -5

    severe = rule_based_affection("滚开，废物", "上一轮回答")
    assert severe.delta == -10

    neutral = rule_based_affection("那我再问一个问题", "上一轮回答")
    assert neutral.delta == 0


def test_positive_feedback_can_progress_without_reply_segment():
    assert rule_based_affection("谢谢，回答得很好", "").delta == 3
    assert rule_based_affection("好可爱，晚安", "").delta == 1


def test_more_realistic_insults_have_immediate_penalties():
    assert rule_based_affection("你这个傻屌机器人", "").delta == -10
    assert rule_based_affection("臭机器人，真蠢", "").delta <= -5


def test_affection_change_text_reports_real_applied_delta():
    assessment = AffectionAssessment(5, "非常满意")
    assert affection_change_text(98, 100, assessment) == (
        "♡ 好感度 +2（98→100）｜非常满意"
    )



@pytest.mark.asyncio
async def test_hostility_target_is_decided_by_llm():
    class FakeLLM:
        def __init__(self, verdict: str):
            self.verdict = verdict

        async def ask(self, messages):
            return self.verdict

    report = await assess_affection(
        "小丛雨，有人骂你傻逼怎么办",
        "",
        FakeLLM("OTHER"),
        check_hostility_target=True,
        explicit_bot_mention=False,
        persona_names=("小丛雨", "穗织幼刀姬"),
    )
    assert report.delta == 0

    direct = await assess_affection(
        "小丛雨你这个傻逼",
        "",
        FakeLLM("TARGET"),
        check_hostility_target=True,
        explicit_bot_mention=False,
        persona_names=("小丛雨", "穗织幼刀姬"),
    )
    assert direct.delta == -10


@pytest.mark.asyncio
async def test_ambiguous_named_hostility_does_not_penalize_when_llm_unavailable():
    class BrokenLLM:
        async def ask(self, messages):
            raise RuntimeError("offline")

    result = await assess_affection(
        "小丛雨和别人说的那个傻逼到底是谁",
        "",
        BrokenLLM(),
        check_hostility_target=True,
        explicit_bot_mention=False,
        persona_names=("小丛雨",),
    )
    assert result.delta == 0
