from app.services.group_memory_logic import (
    format_group_memory_answer,
    group_memory_reasoning_hints,
    parse_memory_relation,
    resolve_group_memory_question,
    rewrite_first_person_identity_question,
    rewrite_relation_pronouns,
)


def test_simple_is_relation_is_reversible():
    relation = parse_memory_relation("d 是 人")
    assert relation is not None
    assert relation.subject == "d"
    assert relation.object == "人"
    assert relation.kind == "identity"

    answer = resolve_group_memory_question("人是谁", ["d 是 人"])
    assert answer is not None
    assert answer.answers == ("d",)

    answer = resolve_group_memory_question("谁是人", ["d 是 人"])
    assert answer is not None
    assert answer.answers == ("d",)

    answer = resolve_group_memory_question("d是谁", ["d 是 人"])
    assert answer is not None
    assert answer.answers == ("人",)

    answer = resolve_group_memory_question("人指的是谁", ["d 是 人"])
    assert answer is not None
    assert answer.answers == ("d",)


def test_identity_relations_can_follow_short_alias_chains():
    memories = ["d 是 人", "人 就是 hzh"]
    answer = resolve_group_memory_question("hzh是谁", memories)
    assert answer is not None
    assert answer.answers == ("人", "d")


def test_directional_verbs_reverse_subject_and_object_safely():
    memories = ["d 喜欢 猫", "小明讨厌香菜"]

    inverse = resolve_group_memory_question("谁喜欢猫", memories)
    assert inverse is not None
    assert inverse.answers == ("d",)

    forward = resolve_group_memory_question("d喜欢什么", memories)
    assert forward is not None
    assert forward.answers == ("猫",)

    dislike = resolve_group_memory_question("谁讨厌香菜", memories)
    assert dislike is not None
    assert dislike.answers == ("小明",)


def test_role_relations_keep_direction_instead_of_becoming_aliases():
    memories = ["hzh 是 小明的儿子"]
    relation = parse_memory_relation(memories[0])
    assert relation is not None
    assert relation.kind == "role"
    assert relation.subject == "hzh"
    assert relation.object == "小明"
    assert relation.predicate == "儿子"

    answer = resolve_group_memory_question("小明的儿子是谁", memories)
    assert answer is not None
    assert answer.answers == ("hzh",)
    assert resolve_group_memory_question("儿子是谁", memories) is None


def test_natural_memory_answer_contains_fact_and_extra_wording():
    answer = resolve_group_memory_question("人是谁", ["d是人"])
    assert answer is not None
    reply = format_group_memory_answer(answer, "人是谁")
    assert "d" in reply
    assert "人" in reply
    assert reply != "d"
    assert len(reply) > len("人是d")
    assert "群记忆" not in reply
    assert "数据库" not in reply
    assert any(marker in reply for marker in ("吾辈", "苟修金", "汝"))
    assert not reply.startswith(("嗯", "记得", "按之前"))


def test_reasoning_hints_explain_inverse_lookup_without_reversing_roles():
    hints = group_memory_reasoning_hints(
        ["d是人", "小明喜欢猫", "hzh是小明的儿子"]
    )
    assert "d ↔ 人" in hints
    assert "谁喜欢猫" in hints
    assert "小明的儿子是谁" in hints
    assert "不要颠倒亲属/关系方向" in hints


def test_first_person_identity_question_uses_sender_name():
    assert rewrite_first_person_identity_question("你知道我是谁吗", "狄") == "狄是谁"
    assert rewrite_first_person_identity_question("我是谁", "狄") == "狄是谁"
    assert rewrite_first_person_identity_question("你是谁", "狄") == "你是谁"
    assert rewrite_first_person_identity_question("我喜欢猫", "狄") == "我喜欢猫"


def test_sender_identity_question_can_resolve_group_alias():
    rewritten = rewrite_first_person_identity_question("你知道我是谁吗", "狄")
    answer = resolve_group_memory_question(rewritten, ["狄是drj"])
    assert answer is not None
    assert answer.answers == ("drj",)


def test_conjoined_parent_role_is_split_and_intersected():
    relation = parse_memory_relation("狄是drj跟hzh的老爸")
    assert relation is not None
    assert relation.kind == "role"
    assert relation.subject == "狄"
    assert relation.object == "drj"
    assert relation.predicate == "爸爸"

    answer = resolve_group_memory_question(
        "drj跟hzh的老爸是谁",
        ["狄是drj跟hzh的老爸"],
    )
    assert answer is not None
    assert answer.kind == "role"
    assert answer.answers == ("狄",)


def test_legacy_unbound_first_person_memory_is_not_used_as_certain_fact():
    assert resolve_group_memory_question(
        "drj跟hzh的老爸是谁",
        ["我是drj跟hzh的老爸"],
    ) is None



def test_relation_pronouns_bind_to_speaker_and_bot_without_reversing():
    assert rewrite_relation_pronouns("我的爸爸是谁", "狄", "小丛雨") == "狄的爸爸是谁"
    assert rewrite_relation_pronouns("我喜欢谁", "狄", "小丛雨") == "狄喜欢谁"
    assert rewrite_relation_pronouns("谁喜欢我", "狄", "小丛雨") == "谁喜欢狄"
    assert rewrite_relation_pronouns("你的朋友是谁", "狄", "小丛雨") == "小丛雨的朋友是谁"
    assert rewrite_relation_pronouns("谁讨厌你", "狄", "小丛雨") == "谁讨厌小丛雨"


def test_bound_relation_question_resolves_correct_subject_object():
    memories = ["小明是狄的爸爸", "狄喜欢猫", "小红喜欢狄"]
    parent_q = rewrite_relation_pronouns("我的爸爸是谁", "狄", "小丛雨")
    parent = resolve_group_memory_question(parent_q, memories)
    assert parent is not None
    assert parent.answers == ("小明",)

    forward_q = rewrite_relation_pronouns("我喜欢谁", "狄", "小丛雨")
    forward = resolve_group_memory_question(forward_q, memories)
    assert forward is not None
    assert forward.answers == ("猫",)

    inverse_q = rewrite_relation_pronouns("谁喜欢我", "狄", "小丛雨")
    inverse = resolve_group_memory_question(inverse_q, memories)
    assert inverse is not None
    assert inverse.answers == ("小红",)
