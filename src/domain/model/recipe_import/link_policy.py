"""Find, validate, normalise and classify links a user imports from.

This is the first, DNS-free gate. The fetcher re-checks every resolved
address, so a hostname that resolves to a private network is still refused.
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from urllib.parse import parse_qs, urlsplit, urlunsplit

from src.domain.exceptions.recipe_import_exceptions import (
    RecipeImportError,
    RecipeImportFailure,
)
from src.domain.model.recipe_import.enums import RecipeSourcePlatform
from src.domain.model.recipe_import.limits import MAX_RECIPE_URL_LENGTH

_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,;:!?)]}>\"'»”’"
_ALLOWED_PORTS = frozenset({80, 443})
_NUMERIC_LABEL = re.compile(r"^(?:\d+|0x[0-9a-f]+)$", re.IGNORECASE)
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".arpa")
_BLOCKED_HOSTS = frozenset({"localhost", "metadata", "metadata.google.internal"})
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_PATH_PREFIXES = ("shorts", "live", "embed", "v", "e")

_PLATFORM_DOMAINS: tuple[tuple[RecipeSourcePlatform, tuple[str, ...]], ...] = (
    (RecipeSourcePlatform.TIKTOK, ("tiktok.com",)),
    (RecipeSourcePlatform.INSTAGRAM, ("instagram.com", "instagr.am")),
    (RecipeSourcePlatform.FACEBOOK, ("facebook.com", "fb.watch", "fb.com")),
)
_YOUTUBE_DOMAINS = ("youtube.com", "youtube-nocookie.com")
# Caption links worth opening for the full recipe: at most this many, and
# never to hosts that hold no recipe pages (shops, link lists, socials).
MAX_CAPTION_LINKS = 3
_NOT_RECIPE_DOMAINS = (
    "youtu.be",
    "youtube.com",
    "tiktok.com",
    "instagram.com",
    "facebook.com",
    "fb.watch",
    "threads.net",
    "twitter.com",
    "x.com",
    "pinterest.com",
    "pin.it",
    "linktr.ee",
    "beacons.ai",
    "amazon.com",
    "amzn.to",
    "shopee.vn",
    "lazada.vn",
    "patreon.com",
    "spotify.com",
    "apple.com",
    "google.com",
)
_RECIPE_LINK_WORDS = re.compile(
    r"recipe|recette|receta|rezept|ricetta|resep|cong-thuc|cach-lam",
    re.IGNORECASE,
)


def extract_recipe_url(raw: str) -> str:
    """Return the normalised link inside share-sheet text or a bare link."""
    text = (raw or "").strip()
    match = _URL_IN_TEXT.search(text)
    if match:
        candidate = _trim_link(match.group(0))
    elif text and " " not in text and "." in text and "://" not in text:
        candidate = f"https://{text}"
    else:
        raise RecipeImportError(
            RecipeImportFailure.INVALID_SOURCE, "No http or https link was found."
        )
    return normalize_recipe_url(candidate)


def caption_recipe_links(caption: str | None) -> tuple[str, ...]:
    """Links in a video caption that may lead to the full recipe, best first.

    Creators often write "full recipe: https://…". Links to video platforms,
    shops and link lists, and links the server must not fetch, are skipped;
    links that look like recipe pages come first.
    """
    found: list[str] = []
    for match in _URL_IN_TEXT.finditer(caption or ""):
        try:
            url = normalize_recipe_url(_trim_link(match.group(0)))
        except RecipeImportError:
            continue
        host = (urlsplit(url).hostname or "").lower()
        if _matches(host, _NOT_RECIPE_DOMAINS) or url in found:
            continue
        found.append(url)
    found.sort(key=lambda url: _RECIPE_LINK_WORDS.search(url) is None)
    return tuple(found[:MAX_CAPTION_LINKS])


def _trim_link(raw: str) -> str:
    """Cut a link found in text at the first character a URL never holds.

    Captions glue invisible separators, emoji and punctuation to links.
    """
    for index, char in enumerate(raw):
        if unicodedata.category(char)[0] in ("C", "S", "Z") and not char.isascii():
            raw = raw[:index]
            break
    return raw.rstrip(_TRAILING_PUNCTUATION + "…")


def normalize_recipe_url(url: str) -> str:
    """Reject links the server must not fetch; drop the fragment."""
    if len(url) > MAX_RECIPE_URL_LENGTH:
        raise _invalid("The link is too long.")
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise _invalid("The link is not a valid URL.") from exc
    if parts.scheme.lower() not in ("http", "https"):
        raise _not_allowed("Only http and https links can be imported.")
    if parts.username is not None or parts.password is not None:
        raise _not_allowed("Links with a user name or password are not allowed.")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise _invalid("The link has no host name.")
    if port is not None and port not in _ALLOWED_PORTS:
        raise _not_allowed("Links on non-standard ports are not allowed.")
    if is_blocked_host(host):
        raise _not_allowed("This link points to a private or local address.")
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise _invalid("The link has an invalid host name.") from exc
    netloc = ascii_host if port is None else f"{ascii_host}:{port}"
    return urlunsplit(
        (parts.scheme.lower(), netloc, parts.path or "/", parts.query, "")
    )


def is_blocked_host(host: str) -> bool:
    """Hosts refused before any DNS lookup: IP literals and local names."""
    name = host.strip("[]").rstrip(".").lower()
    if name in _BLOCKED_HOSTS or name.endswith(_BLOCKED_SUFFIXES):
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    # Numeric spellings such as 2130706433 or 0x7f.1 resolve to IPs too; no
    # real top-level domain is numeric.
    if _NUMERIC_LABEL.match(name.rsplit(".", 1)[-1]):
        return True
    return "." not in name


def classify_recipe_url(url: str) -> tuple[RecipeSourcePlatform, str]:
    """Platform of a normalised link, and the link to fetch.

    YouTube links are rewritten to the canonical watch URL so playlists and
    channels never reach the video reader; a YouTube link without a video id
    is read as an ordinary web page.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host == "youtu.be" or _matches(host, _YOUTUBE_DOMAINS):
        video_id = youtube_video_id(url)
        if video_id:
            return (
                RecipeSourcePlatform.YOUTUBE,
                f"https://www.youtube.com/watch?v={video_id}",
            )
        return RecipeSourcePlatform.WEBSITE, url
    for platform, domains in _PLATFORM_DOMAINS:
        if _matches(host, domains):
            return platform, url
    return RecipeSourcePlatform.WEBSITE, url


def youtube_video_id(url: str) -> str | None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    segments = [segment for segment in parts.path.split("/") if segment]
    candidate: str | None = None
    if host == "youtu.be":
        candidate = segments[0] if segments else None
    elif _matches(host, _YOUTUBE_DOMAINS):
        if segments[:1] == ["watch"]:
            values = parse_qs(parts.query).get("v")
            candidate = values[0] if values else None
        elif len(segments) >= 2 and segments[0] in _YOUTUBE_PATH_PREFIXES:
            candidate = segments[1]
    if candidate and _YOUTUBE_ID.match(candidate):
        return candidate
    return None


def _matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def _invalid(message: str) -> RecipeImportError:
    return RecipeImportError(RecipeImportFailure.INVALID_SOURCE, message)


def _not_allowed(message: str) -> RecipeImportError:
    return RecipeImportError(RecipeImportFailure.URL_NOT_ALLOWED, message)
