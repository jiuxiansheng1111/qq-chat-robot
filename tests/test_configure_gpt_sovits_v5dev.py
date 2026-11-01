from scripts.configure_gpt_sovits_v5dev import reference_transcript, set_values


def test_same_filename_in_another_language_cannot_supply_the_reference_transcript(tmp_path):
    japanese = tmp_path / "japanese"
    chinese = tmp_path / "chinese"
    for directory in (japanese, chinese):
        directory.mkdir()
    wanted = japanese / "voice_001.wav"
    other = chinese / wanted.name
    old_list, correct_list = chinese / "voice.list", japanese / "voice.list"
    old_list.write_text(f"{other}|voice|zh|Wrong transcript\n", encoding="utf-8")
    correct_list.write_text(f"{wanted}|voice|ja|Correct transcript\n", encoding="utf-8")
    assert reference_transcript(wanted, [old_list, correct_list]) == ("ja", "Correct transcript")
    assert reference_transcript(wanted, [old_list]) is None


def test_migration_removes_overrides_without_altering_other_settings():
    before = "# local settings\nVOICE_ENABLED=true\nOTHER_SETTING=keep\nVOICE_ENABLED=false\n"
    after = set_values(before, {"VOICE_ENABLED": "true"})
    assert after.count("VOICE_ENABLED=") == 1
    assert "VOICE_ENABLED=true" in after
    assert "OTHER_SETTING=keep" in after
