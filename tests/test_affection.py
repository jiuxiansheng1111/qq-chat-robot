from app.services.affection import (
    AFFECTION_INITIAL,
    MEMORY_UNLOCK_SCORE,
    AffectionAssessment,
    affection_change_text,
    affection_prompt,
    affection_stage,
    affection_status_text,
    rule_based_affection,
)


def test_affection_defaults_and_unlock_threshold():
    assert AFFECTION_INITIAL == 30
    assert MEMORY_UNLOCK_SCORE == 60
    assert "30/100" in affection_status_text(30)
    assert "60" in affection_status_text(30)


def test_affection_stage_changes_reply_behavior():
    assert affection_stage(30)[0] == "高冷傲娇"
    assert affection_stage(65)[0] == "亲近"
    assert affection_stage(90)[0] == "十分亲密"
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


def test_positive_feedback_requires_previous_bot_reply():
    assert rule_based_affection("谢谢，回答得很好", "").delta == 0
    assert rule_based_affection("谢谢，回答得很好", "上一轮回答").delta > 0


def test_affection_change_text_reports_real_applied_delta():
    assessment = AffectionAssessment(5, "非常满意")
    assert affection_change_text(98, 100, assessment) == (
        "♡ 好感度 +2（98→100）｜非常满意"
    )
