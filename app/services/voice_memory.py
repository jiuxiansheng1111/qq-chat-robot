"""Release the managed local TTS process while a cover owns the shared GPU slot."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger("qqchat")


def is_local_gpt_sovits(settings) -> bool:
    if str(getattr(settings, "voice_provider", "")).casefold() not in {"gpt_sovits", "gpt-sovits"}:
        return False
    try:
        url = urlsplit(str(getattr(settings, "voice_api_url", "")))
        return url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"} and (url.port is None or url.port>0)
    except ValueError:
        return False


async def _powershell(script: str, arguments: list[str], *, timeout: float) -> str:
    log_dir = PROJECT_ROOT / "data/logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / ("voice-memory-" + Path(script).stem + ".log")
    environment=dict(os.environ)
    if os.name=="nt":
        environment["PSModulePath"]=str(Path(os.environ["SystemRoot"])/"System32/WindowsPowerShell/v1.0/Modules")+";"+str(
            Path(os.environ.get("ProgramFiles","C:/Program Files"))/"WindowsPowerShell/Modules")
    with log.open("wb") as output:
        process = await asyncio.create_subprocess_exec(
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(PROJECT_ROOT / "scripts" / script), *arguments,
            cwd=PROJECT_ROOT, stdin=asyncio.subprocess.DEVNULL,
            stdout=output, stderr=asyncio.subprocess.STDOUT, creationflags=0x08000000,
            env=environment,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout)
        finally:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
    result = log.read_text(encoding="utf-8-sig", errors="replace")
    if process.returncode:
        raise RuntimeError(f"Managed voice lifecycle failed; see {log.name}")
    return result


class VoiceMemorySession:
    def __init__(self, settings):
        self.settings = settings
        self.attempted = False
        self.restore_required = False
        self.stop_result: dict = {}
        self.restored = False

    async def pause(self) -> None:
        if self.attempted or os.name != "nt" or not is_local_gpt_sovits(self.settings):
            return
        self.attempted = True
        from dotenv import dotenv_values

        values = dotenv_values(PROJECT_ROOT / ".env")
        root = Path(values.get("GPT_SOVITS_ROOT") or "../qq-chatrobot-voice/GPT-SoVITS")
        if not root.is_absolute():
            root = PROJECT_ROOT / root
        port = urlsplit(self.settings.voice_api_url).port or 80
        # If cancellation lands after the child exits but before JSON is read,
        # the finalizer must still restore the service.
        self.restore_required = True
        output = await _powershell(
            "stop_gpt_sovits_for_singing.ps1", ["-Port", str(port), "-GptRoot", str(root.resolve())], timeout=25,
        )
        self.stop_result = json.loads(output.strip())
        self.restore_required = self.stop_result.get("stopped") is True
        logger.info("singing_voice_memory_release %s", json.dumps(self.stop_result))

    async def restore(self) -> None:
        if not self.restore_required:
            return
        # The cover's GPU lock has been released before this finalizer runs.
        await _powershell("start_voice.ps1", [], timeout=180)
        url = urlsplit(self.settings.voice_api_url)
        deadline = asyncio.get_running_loop().time() + 95
        while True:
            try:
                _reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(url.hostname, url.port or 80), 2,
                )
                writer.close()
                await writer.wait_closed()
                break
            except (OSError, TimeoutError):
                if asyncio.get_running_loop().time() >= deadline:
                    raise RuntimeError("Speech did not become ready after singing") from None
                await asyncio.sleep(.5)
        self.restore_required = False
        self.restored = True
        logger.info("singing_voice_memory_restored")


@asynccontextmanager
async def release_voice_memory_for_singing(settings):
    session = VoiceMemorySession(settings)
    try:
        yield session
    finally:
        if session.restore_required:
            cleanup = asyncio.create_task(session.restore())
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                with suppress(Exception):
                    await cleanup
                raise
            except Exception:
                # A failed TTS restart must not discard a successfully made cover
                # or hide the original conversion/resource error.
                logger.exception("Could not restore local speech after singing; one-click start can retry")
