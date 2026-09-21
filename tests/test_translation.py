from app.services.translation import (
    TranslationResult,
    format_translation_reply,
    needs_translation,
    parse_translation_result,
    translated_name_context,
)


def test_needs_translation_detects_common_foreign_scripts():
    assert needs_translation("星街すいせい")
    assert needs_translation("안녕하세요")
    assert needs_translation("Привет")
    assert not needs_translation("星街彗星")
    assert not needs_translation("Hoshimachi Suisei")


def test_parse_translation_result_accepts_fenced_json_and_deduplicates_aliases():
    result = parse_translation_result(
        '"""{\n'
        '"translated":"星街彗星",'
        '"romanized":"Hoshimachi Suisei",'
        '"aliases":["星街彗星","Hoshimachi Suisei","Hoshimachi Suisei"]'
        '}"""'
    )
    assert result is not None
    assert result.translated == "星街彗星"
    assert result.romanized == "Hoshimachi Suisei"
    assert result.aliases == ("星街彗星", "Hoshimachi Suisei")


def test_query_variants_preserve_original_and_remove_duplicate_spellings():
    result = TranslationResult(
        translated="星街彗星",
        romanized="Hoshimachi Suisei",
        aliases=("星街すいせい", "HOSHIMACHI SUISEI"),
    )
    assert result.query_variants("星街すいせい") == [
        "星街彗星",
        "Hoshimachi Suisei",
    ]


def test_translated_name_context_keeps_group_card_as_source_of_truth():
    result = TranslationResult(
        translated="星街彗星",
        romanized="Hoshimachi Suisei",
        aliases=(),
    )
    note = translated_name_context("星街すいせい", result)
    assert "群名片原文：星街すいせい" in note
    assert "中文参考：星街彗星" in note
    assert "罗马字：Hoshimachi Suisei" in note


def test_translation_reply_keeps_original_text():
    result = TranslationResult(
        translated="星街彗星",
        romanized="Hoshimachi Suisei",
        aliases=(),
    )
    reply = format_translation_reply("星街すいせい", result)
    assert "原文：星街すいせい" in reply
    assert "翻译：星街彗星" in reply
    assert "罗马字：Hoshimachi Suisei" in reply
