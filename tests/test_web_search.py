from app.services.web_search import parse_bing_rss, parse_duckduckgo_html


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


def test_parse_duckduckgo_html_unwraps_redirect_and_snippet():
    payload = """
    <div class="result">
      <a class="result__a"
         href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fweather">
         Singapore Weather
      </a>
      <a class="result__snippet">Current temperature and forecast.</a>
    </div>
    """
    results = parse_duckduckgo_html(payload)
    assert len(results) == 1
    assert results[0].title == "Singapore Weather"
    assert results[0].url == "https://example.com/weather"
    assert results[0].snippet == "Current temperature and forecast."
