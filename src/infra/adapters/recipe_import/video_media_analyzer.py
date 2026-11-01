"""Download a recipe video and read what it says and shows.

Runs only when the caption is not enough. The download is capped in size
and duration, work files live in a private temp directory, and a process
semaphore bounds how many videos are processed at once.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.domain.model.recipe_import.enums import RecipeSourcePlatform
from src.domain.model.recipe_import.limits import MAX_TRANSCRIPT_CHARS
from src.domain.model.recipe_import.source_material import VideoMediaAnalysis
from src.domain.model.recipe_import.text import clean_text
from src.infra.adapters.recipe_import.audio_transcriber import OpenAIAudioTranscriber
from src.infra.adapters.recipe_import.media_tools import (
    MediaToolError,
    build_contact_sheets,
    extract_audio,
    extract_frames,
    media_tools_available,
)
from src.infra.adapters.recipe_import.recipe_ai_reader import RecipeAiReader
from src.infra.adapters.recipe_import.ytdlp_support import video_error, ytdlp_options

logger = logging.getLogger(__name__)

# Fixed limits: a recipe video is short, and analysis runs in the API process.
MAX_VIDEO_SECONDS = 900
MAX_VIDEO_BYTES = 150 * 1024 * 1024
FRAMES_PER_VIDEO = 8
VIDEOS_AT_ONCE = 2

_DOWNLOAD_FORMAT = (
    "best[height<=720][vcodec!=none][acodec!=none]/"
    "best[vcodec!=none][acodec!=none]/best"
)


class _DownloadCancelled(Exception):
    pass


class VideoMediaAnalyzer:
    def __init__(
        self,
        *,
        reader: RecipeAiReader,
        transcriber: OpenAIAudioTranscriber | None,
        max_seconds: int = MAX_VIDEO_SECONDS,
        max_bytes: int = MAX_VIDEO_BYTES,
        frame_count: int = FRAMES_PER_VIDEO,
        concurrency: int = VIDEOS_AT_ONCE,
        socket_timeout: float = 20.0,
        ytdlp_factory: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self._reader = reader
        self._transcriber = transcriber
        self._max_seconds = max_seconds
        self._max_bytes = max_bytes
        self._frame_count = frame_count
        self._semaphore = asyncio.Semaphore(concurrency)
        self._socket_timeout = socket_timeout
        self._ytdlp_factory = ytdlp_factory or _default_ytdlp

    @property
    def available(self) -> bool:
        return media_tools_available()

    async def analyze(
        self,
        url: str,
        platform: RecipeSourcePlatform,
        *,
        duration_seconds: int | None,
    ) -> VideoMediaAnalysis:
        if duration_seconds and duration_seconds > self._max_seconds:
            logger.info(
                "Recipe video too long to analyze: seconds=%d", duration_seconds
            )
            return VideoMediaAnalysis()
        async with self._semaphore:
            work_dir = Path(tempfile.mkdtemp(prefix="recipe-import-"))
            cancel = threading.Event()
            loop = asyncio.get_running_loop()
            download = loop.run_in_executor(
                None, self._download, url, platform, work_dir, cancel
            )
            try:
                video = await download
                return await self._read(video, work_dir, duration_seconds)
            finally:
                cancel.set()
                if download.done():
                    shutil.rmtree(work_dir, ignore_errors=True)
                else:
                    # The download thread notices the cancel at its next
                    # progress callback; clean up once it has stopped.
                    download.add_done_callback(
                        lambda _: shutil.rmtree(work_dir, ignore_errors=True)
                    )

    async def _read(
        self, video: Path, work_dir: Path, duration_seconds: int | None
    ) -> VideoMediaAnalysis:
        audio_dir = work_dir / "audio"
        frames_dir = work_dir / "frames"
        audio_dir.mkdir()
        frames_dir.mkdir()
        audio, frames = await asyncio.gather(
            extract_audio(video, audio_dir, max_seconds=self._max_seconds),
            self._frames(video, frames_dir, duration_seconds),
        )
        sheets = self._sheets(frames)
        transcript, *frame_texts = await asyncio.gather(
            self._transcribe(audio),
            *(self._frame_text(sheet) for sheet in sheets),
        )
        on_screen = "\n".join(text for text in frame_texts if text) or None
        return VideoMediaAnalysis(
            transcript=clean_text(
                transcript, max_chars=MAX_TRANSCRIPT_CHARS, multiline=True
            ),
            on_screen_text=on_screen,
        )

    @staticmethod
    def _sheets(frames: list[Path]) -> list[bytes]:
        try:
            return build_contact_sheets(frames) if frames else []
        except (OSError, ValueError) as exc:
            logger.warning("Recipe video frames unreadable: %s", type(exc).__name__)
            return []

    async def _frames(
        self, video: Path, frames_dir: Path, duration_seconds: int | None
    ) -> list[Path]:
        try:
            return await extract_frames(
                video,
                frames_dir,
                count=self._frame_count,
                duration=float(duration_seconds) if duration_seconds else None,
            )
        except MediaToolError as exc:
            logger.warning("Recipe video frames failed: %s", type(exc).__name__)
            return []

    async def _transcribe(self, audio: Path | None) -> str | None:
        if audio is None or self._transcriber is None:
            return None
        try:
            return await self._transcriber.transcribe(audio)
        except Exception as exc:  # Speech is one signal of several.
            logger.warning("Recipe video transcription failed: %s", type(exc).__name__)
            return None

    async def _frame_text(self, sheet: bytes) -> str | None:
        try:
            return await self._reader.read_frames(sheet)
        except Exception as exc:  # On-screen text is one signal of several.
            logger.warning("Recipe video frame reading failed: %s", type(exc).__name__)
            return None

    def _download(
        self,
        url: str,
        platform: RecipeSourcePlatform,
        work_dir: Path,
        cancel: threading.Event,
    ) -> Path:
        def stop_when_cancelled(_: dict[str, Any]) -> None:
            if cancel.is_set():
                raise _DownloadCancelled()

        options = ytdlp_options(
            platform,
            socket_timeout=self._socket_timeout,
            format=_DOWNLOAD_FORMAT,
            max_filesize=self._max_bytes,
            outtmpl=str(work_dir / "video.%(ext)s"),
            progress_hooks=[stop_when_cancelled],
            concurrent_fragment_downloads=1,
        )
        try:
            with self._ytdlp_factory(options) as ydl:
                ydl.download([url])
        except _DownloadCancelled:
            raise
        except Exception as exc:
            raise video_error(exc) from exc
        files = [path for path in work_dir.glob("video.*") if path.is_file()]
        if not files:
            raise video_error(RuntimeError("download produced no file"))
        return max(files, key=lambda path: path.stat().st_size)


def _default_ytdlp(options: dict[str, Any]) -> Any:
    from yt_dlp import YoutubeDL  # type: ignore[import-untyped]

    return YoutubeDL(options)
