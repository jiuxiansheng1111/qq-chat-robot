import asyncio
import io
import json
from itertools import pairwise
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

import app.services.giphy_cat as giphy
from app.plugins import media
from app.services.giphy_cat import (
    GIPHY_CAT_PAGE,
    _animated_gif,
    _gif_url,
    giphy_cat_candidates,
    random_giphy_cat_gif,
)


def page_payload(items):
    payload = '0:' + json.dumps({'gifs': items}) + '\n'
    return '<script>self.__next_f.push([1,' + json.dumps(payload) + '])</script>'


def cat_item(identity='cat-one', title='cat GIF'):
    return {'id': identity, 'title': title, 'type': 'gif', 'images': {
        'original': {'url': f'https://media0.giphy.com/media/{identity}/giphy.gif',
                     'size': '100', 'frames': '2'},
    }}


@pytest.fixture(autouse=True)
def clear_history():
    giphy._RECENT_IDS.clear()
    giphy._RECENT_CONTENT.clear()
    giphy._IN_FLIGHT_IDS.clear()
    giphy._PAGE_CANDIDATES.clear()
    yield
    giphy._RECENT_IDS.clear()
    giphy._RECENT_CONTENT.clear()
    giphy._IN_FLIGHT_IDS.clear()
    giphy._PAGE_CANDIDATES.clear()


def animated_gif(color='red'):
    output = io.BytesIO()
    Image.new('RGB', (3, 3), color).save(
        output, format='GIF', save_all=True,
        append_images=[Image.new('RGB', (3, 3), 'blue')], duration=100, loop=0,
    )
    return output.getvalue()


def test_candidates_select_cat_gifs_and_keep_url_parameters():
    item = cat_item()
    item['images']['original']['url'] += '?cid=keep-me&ep=share'
    dog = cat_item('dog', 'happy dog GIF')
    still = cat_item('still')
    still['images']['original']['frames'] = '1'
    result = giphy_cat_candidates(page_payload([item, dog, still]), GIPHY_CAT_PAGE, 1024)
    assert len(result) == 1
    assert result[0].url.endswith('?cid=keep-me&ep=share')


def test_candidates_use_smaller_gif_when_original_is_large():
    item = cat_item()
    item['images']['original']['size'] = '9999'
    item['images']['downsized'] = {'url': 'https://media.giphy.com/media/cat-one/small.gif',
                                  'size': '99'}
    result = giphy_cat_candidates(page_payload([item]), GIPHY_CAT_PAGE, 1024)
    assert result[0].url.endswith('small.gif')


def test_meta_fallback_rejects_webp_and_video():
    page = ('<meta property="og:image" content="https://media0.giphy.com/media/id/giphy.webp">'
            '<meta property="og:video" content="https://media0.giphy.com/media/id/giphy.mp4">'
            '<meta property="og:image" content="https://media0.giphy.com/media/id/giphy.gif">')
    result = giphy_cat_candidates(page, GIPHY_CAT_PAGE, 1024)
    assert len(result) == 1
    assert result[0].url.endswith('.gif')


@pytest.mark.parametrize('url', [
    'http://media.giphy.com/media/id/giphy.gif',
    'https://giphy.com.evil.test/image.gif',
    'https://media.giphy.com/media/id/giphy_s.gif',
    'https://media.giphy.com:bad/media/id/giphy.gif',
    'https://account@media.giphy.com/media/id/giphy.gif',
])
def test_gif_url_rejects_other_formats_and_hosts(url):
    assert _gif_url(url) is None


def test_animated_gif_requires_two_frames():
    output = io.BytesIO()
    Image.new('RGB', (2, 2), 'red').save(output, format='GIF')
    assert not _animated_gif(output.getvalue())
    assert not _animated_gif(b'GIF89a-invalid')
    assert _animated_gif(animated_gif())


@pytest.mark.asyncio
async def test_giphy_download_validates_animation_and_returns_original_url(monkeypatch):
    item = cat_item('unique-cat')
    url = item['images']['original']['url']

    def handler(request):
        if request.url.host == 'giphy.com':
            assert request.headers['accept'] == 'text/html'
            return httpx.Response(200, text=page_payload([item]))
        assert request.headers['accept'] == 'image/gif'
        return httpx.Response(200, content=animated_gif(), headers={'content-type': 'image/gif'})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler),
    ))
    result = await random_giphy_cat_gif(SimpleNamespace(media_max_bytes=1024, cat_timeout_seconds=5))
    assert result == url


@pytest.mark.asyncio
async def test_giphy_reuses_oldest_after_pool_is_seen_without_immediate_repeat(monkeypatch):
    giphy._RECENT_IDS.clear()
    giphy._IN_FLIGHT_IDS.clear()
    items = [cat_item('cat-A'), cat_item('cat-B')]

    def handler(request):
        if request.url.host == 'giphy.com':
            return httpx.Response(200, text=page_payload(items))
        color = 'red' if 'cat-A' in request.url.path else 'green'
        return httpx.Response(200, content=animated_gif(color))

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler),
    ))
    settings = SimpleNamespace(media_max_bytes=1024, cat_timeout_seconds=5)
    values = [await random_giphy_cat_gif(settings) for _ in range(5)]
    assert all(first != second for first, second in pairwise(values))


@pytest.mark.asyncio
async def test_different_links_with_same_content_are_skipped(monkeypatch):
    items = [cat_item('cat-A'), cat_item('cat-alias'), cat_item('cat-B')]

    def handler(request):
        if request.url.host == 'giphy.com':
            return httpx.Response(200, text=page_payload(items))
        color = 'green' if 'cat-B' in request.url.path else 'red'
        return httpx.Response(200, content=animated_gif(color))

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler),
    ))
    monkeypatch.setattr(giphy.random, 'SystemRandom', lambda: SimpleNamespace(choice=lambda values: values[0]))
    settings = SimpleNamespace(media_max_bytes=1024, cat_timeout_seconds=5)
    assert 'cat-A/' in await random_giphy_cat_gif(settings)
    assert 'cat-B/' in await random_giphy_cat_gif(settings)


@pytest.mark.asyncio
async def test_single_unchanged_gif_does_not_repeat(monkeypatch):
    def handler(request):
        if request.url.host == 'giphy.com':
            return httpx.Response(200, text=page_payload([cat_item('only-cat')]))
        return httpx.Response(200, content=animated_gif())

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler),
    ))
    settings = SimpleNamespace(media_max_bytes=1024, cat_timeout_seconds=5)
    await random_giphy_cat_gif(settings)
    with pytest.raises(RuntimeError, match='没有可用'):
        await random_giphy_cat_gif(settings)


@pytest.mark.asyncio
async def test_cat_giphy_has_priority_and_cataas_is_fallback(monkeypatch):
    settings = SimpleNamespace(cat_giphy_enabled=True)
    media._cat_gif_cache.clear()
    media._cat_gif_cache.append('base64://cached-fallback')

    async def no_refill(_):
        return None

    async def primary(_):
        return 'https://media0.giphy.com/media/cat/giphy.gif'

    monkeypatch.setattr(media, 'warm_cat_gif_cache', no_refill)
    monkeypatch.setattr(media, 'random_giphy_cat_gif', primary)
    assert (await media.random_cat_gif(settings)).startswith('https://')
    assert len(media._cat_gif_cache) == 1

    async def failed(_):
        raise RuntimeError('upstream offline')

    monkeypatch.setattr(media, 'random_giphy_cat_gif', failed)
    assert await media.random_cat_gif(settings) == 'base64://cached-fallback'
    await asyncio.sleep(0)
