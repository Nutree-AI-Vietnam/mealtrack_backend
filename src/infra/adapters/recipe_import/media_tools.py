"""ffmpeg helpers and frame contact sheets for recipe videos."""

from __future__ import annotations

import asyncio
import io
import math
import shutil
from pathlib import Path

from PIL import Image

_FFMPEG_LOG = ("-hide_banner", "-loglevel", "error", "-nostdin")
_SHEET_FRAMES = 4
_JPEG_QUALITY = 85


class MediaToolError(RuntimeError):
    """ffmpeg or ffprobe failed or is missing."""


def media_tools_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


async def _run(binary: str, args: list[str], *, timeout: float) -> bytes:
    path = shutil.which(binary)
    if path is None:
        raise MediaToolError(f"{binary} is not installed")
    process = await asyncio.create_subprocess_exec(
        path,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError as exc:
        await _kill(process)
        raise MediaToolError(f"{binary} timed out") from exc
    except BaseException:
        await _kill(process)
        raise
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise MediaToolError(detail[-1][:200] if detail else f"{binary} failed")
    return stdout


async def _kill(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.kill()
        await process.wait()


async def probe_duration(video: Path, *, timeout: float = 15.0) -> float | None:
    output = await _run(
        "ffprobe",
        [
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(video),
        ],
        timeout=timeout,
    )
    try:
        duration = float(output.decode().strip())
    except ValueError:
        return None
    return duration if math.isfinite(duration) and duration > 0 else None


async def extract_audio(
    video: Path, work_dir: Path, *, max_seconds: int, timeout: float = 60.0
) -> Path | None:
    """Mono 16 kHz MP3, small enough for the speech API; None without audio."""
    out = work_dir / "audio.mp3"
    try:
        await _run(
            "ffmpeg",
            [
                *_FFMPEG_LOG,
                "-i",
                str(video),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-b:a",
                "32k",
                "-t",
                str(max_seconds),
                "-y",
                str(out),
            ],
            timeout=timeout,
        )
    except MediaToolError:
        # A video without an audio stream has nothing to transcribe.
        return None
    return out if out.exists() and out.stat().st_size > 0 else None


async def extract_frames(
    video: Path,
    work_dir: Path,
    *,
    interval_seconds: float,
    max_frames: int,
    duration: float | None,
    timeout: float = 90.0,
) -> list[Path]:
    """One frame every ``interval_seconds``, spread wider for long videos.

    Short videos show each caption for only a few seconds, so frames are
    taken densely rather than a fixed handful per video. Frames are 720 px on
    the long edge.
    """
    if max_frames <= 0:
        return []
    if duration is None:
        duration = await probe_duration(video)
    span = max(duration or 0.0, 1.0)
    fps = min(1 / interval_seconds, max_frames / span)
    pattern = work_dir / "frame_%03d.jpg"
    await _run(
        "ffmpeg",
        [
            *_FFMPEG_LOG,
            "-i",
            str(video),
            "-vf",
            f"fps={fps:.6f},scale='if(gt(iw,ih),720,-2)':'if(gt(iw,ih),-2,720)'",
            "-frames:v",
            str(max_frames),
            "-q:v",
            "3",
            "-y",
            str(pattern),
        ],
        timeout=timeout,
    )
    return sorted(work_dir.glob("frame_*.jpg"))[:max_frames]


def build_contact_sheets(
    frames: list[Path], *, frames_per_sheet: int = _SHEET_FRAMES
) -> list[bytes]:
    """JPEG strips of up to ``frames_per_sheet`` frames, in video order.

    Portrait frames sit side by side and landscape frames one above the
    other: after the vision model scales the image down to 768 px on its
    short side, each frame keeps the most pixels, so small captions stay
    readable.
    """
    sheets: list[bytes] = []
    for start in range(0, len(frames), frames_per_sheet):
        group = frames[start : start + frames_per_sheet]
        images = []
        for frame in group:
            with Image.open(frame) as image:
                images.append(image.convert("RGB"))
        if images:
            sheets.append(_strip(images))
    return sheets


def _strip(images: list[Image.Image]) -> bytes:
    width, height = images[0].size
    portrait = height >= width
    count = len(images)
    size = (width * count, height) if portrait else (width, height * count)
    sheet = Image.new("RGB", size, "white")
    for index, image in enumerate(images):
        if image.size != (width, height):
            image = image.resize((width, height))
        offset = (index * width, 0) if portrait else (0, index * height)
        sheet.paste(image, offset)
    buffer = io.BytesIO()
    sheet.save(buffer, format="JPEG", quality=_JPEG_QUALITY)
    return buffer.getvalue()
