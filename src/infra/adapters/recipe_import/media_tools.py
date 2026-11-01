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
    count: int,
    duration: float | None,
    timeout: float = 60.0,
) -> list[Path]:
    """``count`` frames spread evenly over the video, 720 px on the long edge."""
    if duration is None:
        duration = await probe_duration(video)
    span = max(duration or 0.0, 1.0)
    fps = count / span
    pattern = work_dir / "frame_%02d.jpg"
    await _run(
        "ffmpeg",
        [
            *_FFMPEG_LOG,
            "-i",
            str(video),
            "-vf",
            f"fps={fps:.6f},scale='if(gt(iw,ih),720,-2)':'if(gt(iw,ih),-2,720)'",
            "-frames:v",
            str(count),
            "-q:v",
            "3",
            "-y",
            str(pattern),
        ],
        timeout=timeout,
    )
    return sorted(work_dir.glob("frame_*.jpg"))[:count]


def build_contact_sheets(
    frames: list[Path], *, frames_per_sheet: int = _SHEET_FRAMES
) -> list[bytes]:
    """JPEG grids of up to ``frames_per_sheet`` frames, in video order.

    Few frames per sheet keep on-screen text legible after the vision model
    downsizes the image.
    """
    sheets: list[bytes] = []
    for start in range(0, len(frames), frames_per_sheet):
        group = frames[start : start + frames_per_sheet]
        images = []
        for frame in group:
            with Image.open(frame) as image:
                images.append(image.convert("RGB"))
        if images:
            sheets.append(_grid(images))
    return sheets


def _grid(images: list[Image.Image]) -> bytes:
    width, height = images[0].size
    portrait = height >= width
    columns = min(len(images), 2 if portrait or len(images) > 2 else len(images))
    columns = max(columns, 1)
    rows = math.ceil(len(images) / columns)
    cell_w = 540 if portrait else 720
    cell_h = max(1, round(cell_w * height / width))
    sheet = Image.new("RGB", (cell_w * columns, cell_h * rows), "white")
    for index, image in enumerate(images):
        resized = image.resize((cell_w, cell_h))
        sheet.paste(resized, ((index % columns) * cell_w, (index // columns) * cell_h))
    buffer = io.BytesIO()
    sheet.save(buffer, format="JPEG", quality=_JPEG_QUALITY)
    return buffer.getvalue()
