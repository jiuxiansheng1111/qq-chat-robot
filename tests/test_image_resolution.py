from app.services.image_resolution import ImageResolution, append_image_attribution


def test_attribution_caption_keeps_traceable_source_page_link():
    result = ImageResolution(
        data="base64://example",
        provider="萌娘百科",
        source_page_url="https://zh.moegirl.org.cn/丛雨",
        image_url="https://img.example/murasame.jpg",
        label="丛雨",
    )
    caption = append_image_attribution("角色资料", result)
    assert "图片来源：萌娘百科（丛雨）" in caption
    assert result.source_page_url in caption


def test_legacy_cache_metadata_is_explicit_and_not_traceable():
    result = ImageResolution.from_cache_metadata("base64://example", None)
    assert result.provider == "legacy/unknown"
    assert result.cache_hit is True
    assert result.source_page_url == ""
