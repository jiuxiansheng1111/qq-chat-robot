from app.services.web_search import parse_bing_rss


def test_parse_bing_rss_filters_and_cleans_results():
    payload = """<?xml version="1.0"?>
    <rss><channel>
      <item><title>Python &amp; News</title><link>https://example.com/python</link>
      <description><![CDATA[<b>Useful</b> summary]]></description></item>
      <item><title>Unsafe</title><link>javascript:alert(1)</link><description>x</description></item>
    </channel></rss>"""
    results = parse_bing_rss(payload)
    assert len(results) == 1
    assert results[0].title == "Python & News"
    assert results[0].snippet == "Useful summary"
    assert results[0].url == "https://example.com/python"
