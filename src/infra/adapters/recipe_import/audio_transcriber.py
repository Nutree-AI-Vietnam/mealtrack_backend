"""Speech to text for recipe videos through the OpenAI audio API."""

from __future__ import annotations

import logging
from pathlib import Path

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# The audio endpoint refuses files above 25 MB.
MAX_TRANSCRIPTION_BYTES = 24 * 1024 * 1024
_RECIPE_PROMPT = (
    "A cooking video: ingredient names, quantities, units, temperatures and "
    "cooking steps."
)


class OpenAIAudioTranscriber:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._client = client or AsyncOpenAI(
            api_key=api_key, timeout=timeout_seconds, max_retries=1
        )
        self._model = model

    async def transcribe(self, audio_path: Path) -> str | None:
        """Return the transcript, or None for silence or a file too large."""
        size = audio_path.stat().st_size
        if size == 0 or size > MAX_TRANSCRIPTION_BYTES:
            logger.info("Recipe video audio skipped: bytes=%d", size)
            return None
        with audio_path.open("rb") as audio:
            result = await self._client.audio.transcriptions.create(
                model=self._model,
                file=audio,
                prompt=_RECIPE_PROMPT,
                response_format="text",
            )
        text = result if isinstance(result, str) else getattr(result, "text", "")
        return text.strip() or None
