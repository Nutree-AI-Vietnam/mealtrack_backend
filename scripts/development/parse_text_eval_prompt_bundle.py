"""Explicit localized prompt bundle loading and rendered hashes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def load_prompt_bundle(
    path: str | Path, family: str | None = None
) -> tuple[dict[str, str], str]:
    source = Path(path).expanduser().resolve().read_text(encoding="utf-8")
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    try:
        value = json.loads(source)
    except json.JSONDecodeError:
        value = source
    if isinstance(value, str) and value.strip():
        return {"*": value}, digest
    if isinstance(value, dict) and isinstance(value.get("prompts"), dict):
        prompts = {}
        for key, entry in value["prompts"].items():
            if (
                not isinstance(key, str)
                or ":" not in key
                or not isinstance(entry, dict)
            ):
                continue
            entry_family, language = key.split(":", 1)
            if family is not None and entry_family != family:
                continue
            text = entry.get("text")
            expected_hash = entry.get("sha256")
            if (
                not isinstance(text, str)
                or not text.strip()
                or not isinstance(expected_hash, str)
            ):
                raise ValueError(
                    f"prompt snapshot entry {key!r} requires text and sha256"
                )
            actual_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if actual_hash != expected_hash:
                raise ValueError(f"prompt snapshot hash mismatch for {key!r}")
            prompts[language] = text
        if not prompts:
            raise ValueError(f"prompt snapshot has no entries for family {family!r}")
        return prompts, digest
    if (
        isinstance(value, dict)
        and value
        and all(
            isinstance(language, str) and isinstance(prompt, str) and prompt.strip()
            for language, prompt in value.items()
        )
    ):
        return value, digest
    raise ValueError(
        "prompt bundle must be text or a non-empty language-to-prompt JSON object"
    )


def _prompt_for_language(bundle: dict[str, str], language: str) -> str:
    prompt = bundle.get(language) or bundle.get("*")
    if not prompt:
        raise ValueError(
            f"prompt bundle has no rendered prompt for language {language!r}"
        )
    return prompt


def _rendered_prompt_hashes(
    bundle: dict[str, str], languages: set[str]
) -> dict[str, str]:
    return {
        language: hashlib.sha256(
            _prompt_for_language(bundle, language).encode("utf-8")
        ).hexdigest()
        for language in sorted(languages)
    }
