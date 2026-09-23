from app.services.simple_logic import resolve_rps_logic, rps_winner


def test_rps_winner_rules():
    assert rps_winner("石头", "剪刀") == "石头"
    assert rps_winner("剪刀", "布") == "剪刀"
    assert rps_winner("布", "石头") == "布"
    assert rps_winner("石头", "石头") == "平局"


def test_rps_comparison_questions_are_deterministic():
    assert resolve_rps_logic("石头和剪刀谁赢") == "石头赢，石头克剪刀。"
    assert resolve_rps_logic("剪刀能赢布吗") == "剪刀赢，剪刀克布。"
    assert resolve_rps_logic("paper vs rock 哪个会赢") == "布赢，布克石头。"


def test_bare_game_name_is_not_misparsed_as_comparison():
    assert resolve_rps_logic("来玩石头剪刀布") is None
