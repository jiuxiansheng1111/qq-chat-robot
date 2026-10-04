"""点歌、候选确认和歌词查询的无角色工作流。"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable

from app.config import Settings
from app.services.music import NeteaseTrack, netease_track_matches_query
from app.services.music_library import (
    fetch_music_covers,
    fetch_music_lyrics,
    fetch_music_playlist,
    search_music_candidates,
)
from app.services.music_presentation import render_music_candidates, render_music_lyrics
from app.services.music_selection import (
    MusicAction,
    MusicSelectionKey,
    MusicSelectionStore,
)

SendText = Callable[[str], Awaitable[None]]
SendImage = Callable[[str], Awaitable[None]]
SendCard = Callable[[NeteaseTrack], Awaitable[None]]

_PLAYLIST_ID_RE = re.compile(r"[0-9]{1,20}")
_LRC_TIME_RE = re.compile(r"\[\d{1,3}:\d{2}(?:\.\d{1,3})?\]")
_LRC_META_RE = re.compile(r"\[[A-Za-z][A-Za-z0-9_-]*\s*:[^\]]*\]")
_LRC_META_LINE_RE = re.compile(r"^\s*\[[A-Za-z][A-Za-z0-9_-]*\s*:[^\]]*\]\s*$")
_MAX_LYRICS_TEXT = 12_000
_LOGGER = logging.getLogger(__name__)


class MusicWorkflow:
    """短期管理点歌流程；所有请求使用独立参数，不读取角色或聊天记忆。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.selection = MusicSelectionStore()
        self.semaphore = asyncio.Semaphore(2)
        self.active_tasks: dict[MusicSelectionKey, asyncio.Task[None]] = {}

    async def aclose(self) -> None:
        self.selection.clear()
        tasks = list(self.active_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.active_tasks.clear()

    async def handle(
        self,
        action: MusicAction,
        key: MusicSelectionKey,
        *,
        send_text: SendText,
        send_image: SendImage,
        send_card: SendCard,
        link_only: bool,
    ) -> None:
        if action.action == "cancel":
            self.selection.cancel(key)
            worker = self.active_tasks.get(key)
            if worker is not None:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
                if self.active_tasks.get(key) is worker:
                    self.active_tasks.pop(key, None)
            await self._emit_text(send_text, "当前点歌候选已清除。")
            return

        if key in self.active_tasks or len(self.active_tasks) >= 2:
            await self._emit_text(send_text, "音乐请求正忙，请稍后再试。")
            return

        async def run() -> None:
            async with self.semaphore:
                await self._dispatch(
                    action, key,
                    send_text=send_text, send_image=send_image, send_card=send_card,
                    link_only=link_only,
                )

        worker = asyncio.create_task(run())
        self.active_tasks[key] = worker
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            # 用户取消只终止子任务；服务关闭时仍传播外层取消。
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
                raise
        except Exception as exc:  # noqa: BLE001 - 只记录类型，不带 URL 或账号内容
            _LOGGER.warning("音乐流程失败：%s", type(exc).__name__)
            await self._emit_text(send_text, "音乐服务暂时不可用，请稍后再试。")
        finally:
            if self.active_tasks.get(key) is worker:
                self.active_tasks.pop(key, None)

    async def _dispatch(
        self,
        action: MusicAction,
        key: MusicSelectionKey,
        *,
        send_text: SendText,
        send_image: SendImage,
        send_card: SendCard,
        link_only: bool,
    ) -> None:
        if action.action == "select":
            await self._select_candidate(
                action, key, send_text=send_text, send_card=send_card, link_only=link_only
            )
        elif action.action == "lyrics_candidate":
            await self._lyrics_candidate(action, key, send_text=send_text, send_image=send_image)
        elif action.action == "search":
            await self._search(
                action,
                key,
                send_text=send_text,
                send_image=send_image,
                send_card=send_card,
                link_only=link_only,
            )
        elif action.action == "lyrics_query":
            await self._lyrics_query(action, key, send_text=send_text, send_image=send_image)
        elif action.action == "playlist":
            await self._playlist(action, key, send_text=send_text, send_image=send_image)
        else:
            await self._emit_text(send_text, self._usage())

    async def _search(
        self,
        action: MusicAction,
        key: MusicSelectionKey,
        *,
        send_text: SendText,
        send_image: SendImage,
        send_card: SendCard,
        link_only: bool,
    ) -> None:
        query = action.query.strip()[:120]
        if not query:
            await self._emit_text(send_text, "请提供歌名或歌手名。用法：点歌 歌名")
            return

        token = self.selection.begin_search(key, query)
        try:
            tracks = await search_music_candidates(query, self.settings)
        except Exception:  # noqa: BLE001 - 搜索器的错误类型由适配层决定
            if self._is_current(key, token):
                await self._emit_text(send_text, "歌曲搜索暂时失败，请稍后再试。")
            return
        if not self._is_current(key, token) or not self.selection.complete(key, token, tracks):
            return
        entry = self.selection.get(key)
        if entry is None or entry.token != token:
            return
        if not entry.tracks:
            await self._emit_text(send_text, "没有搜到可用歌曲，请换个歌名或歌手名试试。")
            return

        if len(entry.tracks) == 1 and netease_track_matches_query(query, entry.tracks[0]):
            track = self.selection.select(key, 1)
            if track is not None and self._is_current(key, token):
                await self._send_track(
                    track,
                    key,
                    token,
                    send_text=send_text,
                    send_card=send_card,
                    link_only=link_only,
                )
            return

        await self._show_candidates(
            key, token, entry.tracks, send_text=send_text, send_image=send_image
        )

    async def _lyrics_query(
        self,
        action: MusicAction,
        key: MusicSelectionKey,
        *,
        send_text: SendText,
        send_image: SendImage,
    ) -> None:
        query = action.query.strip()[:120]
        if not query:
            await self._emit_text(send_text, "请提供歌名或歌手名。用法：查歌词 歌名")
            return

        token = self.selection.begin_search(key, query)
        try:
            tracks = await search_music_candidates(query, self.settings)
        except Exception:  # noqa: BLE001 - 搜索器的错误类型由适配层决定
            if self._is_current(key, token):
                await self._emit_text(send_text, "歌词歌曲搜索暂时失败，请稍后再试。")
            return
        if not self._is_current(key, token) or not self.selection.complete(key, token, tracks):
            return
        entry = self.selection.get(key)
        if entry is None or entry.token != token:
            return
        if not entry.tracks:
            await self._emit_text(send_text, "没有搜到可用歌曲，请换个歌名或歌手名试试。")
            return

        if len(entry.tracks) == 1 and netease_track_matches_query(query, entry.tracks[0]):
            await self._send_lyrics(
                entry.tracks[0], key, token, send_text=send_text, send_image=send_image
            )
            return

        await self._show_candidates(
            key,
            token,
            entry.tracks,
            send_text=send_text,
            send_image=send_image,
            intro="没有唯一精确匹配，请先选定歌曲：",
        )

    async def _playlist(
        self,
        action: MusicAction,
        key: MusicSelectionKey,
        *,
        send_text: SendText,
        send_image: SendImage,
    ) -> None:
        playlist_id = action.query.strip()
        if not _PLAYLIST_ID_RE.fullmatch(playlist_id):
            await self._emit_text(send_text, "用法：歌单 网易云歌单编号或链接")
            return

        token = self.selection.begin_search(key, f"歌单 {playlist_id}")
        try:
            playlist = await fetch_music_playlist(playlist_id, self.settings)
        except Exception:  # noqa: BLE001 - 网络库只返回通用歌单提示
            if self._is_current(key, token):
                await self._emit_text(send_text, "歌单暂时无法读取，请确认编号有效且歌单公开。")
            return
        if not self._is_current(key, token):
            return
        if not self.selection.complete(key, token, playlist.tracks):
            return
        entry = self.selection.get(key)
        if entry is None or entry.token != token:
            return
        shown = len(entry.tracks)
        intro = (
            f"歌单《{playlist.title[:120]}》，共 {playlist.total} 首，先展示 {shown} 首；"
            "选定前不会播放："
        )
        await self._show_candidates(
            key, token, entry.tracks, send_text=send_text, send_image=send_image, intro=intro
        )

    async def _select_candidate(
        self,
        action: MusicAction,
        key: MusicSelectionKey,
        *,
        send_text: SendText,
        send_card: SendCard,
        link_only: bool,
    ) -> None:
        entry = self.selection.get(key)
        if entry is None or not entry.tracks:
            await self._emit_text(send_text, "候选已过期或还没有准备好，请重新点歌。")
            return
        if action.index is None:
            await self._emit_text(send_text, "用法：选歌 1")
            return
        track = self.selection.select(key, action.index)
        if track is None:
            await self._emit_text(send_text, "没有这个候选序号，请按列表中的编号选择。")
            return
        if self._is_current(key, entry.token):
            await self._send_track(
                track,
                key,
                entry.token,
                send_text=send_text,
                send_card=send_card,
                link_only=link_only,
            )

    async def _lyrics_candidate(
        self,
        action: MusicAction,
        key: MusicSelectionKey,
        *,
        send_text: SendText,
        send_image: SendImage,
    ) -> None:
        entry = self.selection.get(key)
        if entry is None or not entry.tracks:
            await self._emit_text(send_text, "候选已过期或还没有准备好，请重新点歌。")
            return
        if action.index is None:
            await self._emit_text(send_text, "用法：查歌词 1")
            return
        track = self.selection.get_candidate(key, action.index)
        if track is None:
            await self._emit_text(send_text, "没有这个候选序号，请按列表中的编号查询。")
            return
        await self._send_lyrics(track, key, entry.token, send_text=send_text, send_image=send_image)

    async def _send_track(
        self,
        track: NeteaseTrack,
        key: MusicSelectionKey,
        token: int,
        *,
        send_text: SendText,
        send_card: SendCard,
        link_only: bool,
    ) -> None:
        if not self._is_current(key, token):
            return
        if link_only:
            await self._emit_text(send_text, f"{self._track_label(track)}\n{track.page_url}")
            return
        try:
            await send_card(track)
        except Exception:  # noqa: BLE001 - 发送适配器只回退文本，不记录异常内容
            if self._is_current(key, token):
                await self._emit_text(
                    send_text, f"已选中：{self._track_label(track)}\n{track.page_url}"
                )
        else:
            if self._is_current(key, token):
                await self._emit_text(send_text, f"卡片无法播放时可打开网易云：{track.page_url}")

    async def _send_lyrics(
        self,
        track: NeteaseTrack,
        key: MusicSelectionKey,
        token: int,
        *,
        send_text: SendText,
        send_image: SendImage,
    ) -> None:
        try:
            lrc = await fetch_music_lyrics(track, self.settings)
        except Exception:  # noqa: BLE001 - 只回歌曲级提示，不记录 URL 或异常内容
            if self._is_current(key, token):
                await self._emit_text(
                    send_text,
                    f"《{self._track_label(track)}》暂无可用歌词，或歌词服务暂时不可用。",
                )
            return
        if not self._is_current(key, token):
            return

        try:
            pages = await render_music_lyrics(track, lrc)
        except Exception:  # noqa: BLE001 - 图片排版失败时回退完整歌词文本
            pages = []
        if not self._is_current(key, token):
            return
        if not pages:
            await self._send_lyrics_text(track, lrc, key, token, send_text)
            return

        try:
            for page in pages:
                if not self._is_current(key, token):
                    return
                await send_image(page)
                if not self._is_current(key, token):
                    return
        except Exception:  # noqa: BLE001 - 图片发送失败时回退完整歌词文本
            await self._send_lyrics_text(track, lrc, key, token, send_text)

    async def _send_lyrics_text(
        self,
        track: NeteaseTrack,
        lrc: str,
        key: MusicSelectionKey,
        token: int,
        send_text: SendText,
    ) -> None:
        if len(lrc) > _MAX_LYRICS_TEXT:
            if self._is_current(key, token):
                await self._emit_text(send_text, "歌词图片生成失败，文本也超过显示上限。")
            return
        text = self._clean_lrc(lrc)
        if not text:
            if self._is_current(key, token):
                await self._emit_text(
                    send_text, f"《{self._track_label(track)}》没有可显示的歌词正文。"
                )
            return
        if self._is_current(key, token):
            await self._emit_text(
                send_text, f"《{self._track_label(track)}》歌词（文字版）：\n{text}"
            )

    async def _show_candidates(
        self,
        key: MusicSelectionKey,
        token: int,
        tracks: tuple[NeteaseTrack, ...],
        *,
        send_text: SendText,
        send_image: SendImage,
        intro: str = "请先确认歌曲，未选定前不会播放：",
    ) -> None:
        if not tracks or not self._is_current(key, token):
            return
        listing = self._candidate_text(tracks, intro)
        try:
            covers = await fetch_music_covers(list(tracks[:5]), self.settings)
            if not self._is_current(key, token):
                return
            image = await render_music_candidates(list(tracks[:5]), covers)
            if not self._is_current(key, token):
                return
            await send_image(image)
            if not self._is_current(key, token):
                return
        except Exception:  # noqa: BLE001 - 图片流程失败时回退候选文字
            if self._is_current(key, token):
                await self._emit_text(send_text, listing)
            return

        if self._is_current(key, token):
            await self._emit_text(send_text, listing)

    @staticmethod
    async def _emit_text(send_text: SendText, text: str) -> None:
        try:
            await send_text(text)
        except Exception:  # noqa: BLE001 - 发送器异常不写入日志
            return

    def _is_current(self, key: MusicSelectionKey, token: int) -> bool:
        entry = self.selection.get(key)
        return entry is not None and entry.token == token

    @staticmethod
    def _track_label(track: NeteaseTrack) -> str:
        title = " ".join(track.title.split())[:120] or "未知歌名"
        artist = " ".join(track.artist.split())[:240] or "未知歌手"
        return f"{title} — {artist}"

    @classmethod
    def _candidate_text(cls, tracks: tuple[NeteaseTrack, ...], intro: str) -> str:
        rows = [f"{index}. {cls._track_label(track)}" for index, track in enumerate(tracks, 1)]
        return f"{intro}\n" + "\n".join(rows) + "\n回复：选歌 N｜查歌词 N｜取消选歌"

    @staticmethod
    def _clean_lrc(lrc: str) -> str:
        lines: list[str] = []
        for source_line in lrc.splitlines():
            if _LRC_META_LINE_RE.fullmatch(source_line):
                continue
            line = _LRC_TIME_RE.sub("", source_line)
            line = _LRC_META_RE.sub("", line).strip()
            if line:
                lines.append(line)
            elif lines and lines[-1]:
                lines.append("")
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    @staticmethod
    def _usage() -> str:
        return "用法：点歌 歌名｜查歌词 歌名｜歌单 歌单编号；候选用选歌 1确认。"
