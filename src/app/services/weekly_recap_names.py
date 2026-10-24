"""The name a weekly recap greets the user by.

The name arrives as a display name (from the user's Google or Apple
account) and a stored first name, which is only the display name's first
word. Word order varies: Vietnamese names are written family name first
("Nguyễn Văn Linh", called Linh) or, on many accounts, given name first
("Linh Nguyễn", "Tuan Van Nguyen"; Apple sign-in always joins given then
family name). Greeting someone by their family name is worse than not
greeting them, so whenever the order can't be told there is no greeting.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from typing import Any

# Longest name that still fits one line of the intro headline at 50pt; a
# longer one would shrink the title or break mid-word.
_NAME_MAX = 12
# Chinese, Japanese and Korean names are used whole; this many characters fit.
_CJK_MAX = 6

# Only ever family names, with and without diacritics.
_SURNAMES = frozenset(
    word.casefold()
    for word in (
        "Nguyễn Trần Lê Phạm Huỳnh Phan Võ Đặng Bùi Đỗ Hồ Ngô Đoàn Trịnh "
        "Trương Đinh Lương Phùng Quách Lưu Tạ "
        "Nguyen Tran Pham Huynh Bui Ngo Doan Quach Luu"
    ).split()
)
# Unaccented family names short enough to be something else (a Western
# given name, a word): a family name at the end of a name, but not enough on
# their own to say a name starts with its family name.
_SHORT_SURNAMES = frozenset({"le", "do", "ho", "vo", "ta"})
# Family names that are as often given names.
_ALSO_GIVEN = frozenset(
    word.casefold()
    for word in (
        "Hoàng Vũ Dương Lý Đào Vương "
        "Hoang Vu Duong Ly Dao Vuong Dang Dinh Luong Truong Trinh Phung"
    ).split()
)
# Vietnamese middle-name markers, never what someone is called. The accented
# forms only occur in Vietnamese names; the plain ones also start Dutch and
# other family names ("Van Gogh"), so they count only next to a Vietnamese
# family name.
_MARKERS = frozenset({"Văn", "Thị"})
_PLAIN_MARKERS = frozenset({"Van", "Thi"})
_HONORIFICS = frozenset(
    "dr mr mrs ms mx prof sir mme mlle herr frau sr sra srta dra".split()
)
# Hiragana and katakana, CJK ideographs, and Hangul syllables.
_CJK_RANGES = ((0x3040, 0x30FF), (0x3400, 0x9FFF), (0xAC00, 0xD7AF))


def recap_first_name(user: Any) -> str | None:
    """The name to greet ``user`` by, or None.

    Reads the display name, which keeps the order the user's name was
    written in, and falls back to the first name only when the display name
    is missing or is not a name at all (a handle, an e-mail).
    """
    for raw in (getattr(user, "display_name", None), getattr(user, "first_name", None)):
        words = _name_words(unicodedata.normalize("NFC", str(raw or "")))
        if words is not None:
            return _greeting(words)
    return None


def _name_words(text: str) -> list[str] | None:
    """The words of a name; None when there is no name or it isn't one.

    Decorations with no letters (emoji, symbols) are dropped. An all
    lower-case name is capitalized, as a headline writes it.
    """
    words = [word for word in text.split() if any(char.isalpha() for char in word)]
    if not words:
        return None
    cjk = [word for word in words if all(_is_cjk(char) for char in word)]
    if cjk:
        return ["".join(cjk)]
    if not all(_is_word(word) or _is_initial(word) for word in words):
        return None
    if all(word.islower() for word in words):
        words = [word[:1].upper() + word[1:] for word in words]
    return words


def _greeting(words: Sequence[str]) -> str | None:
    """The word someone with this name is called, when it can be told."""
    if _is_cjk(words[0][0]):
        return words[0] if len(words[0]) <= _CJK_MAX else None
    titled = len(words) > 1 and words[0].casefold().rstrip(".") in _HONORIFICS
    words = list(words[1:]) if titled else list(words)
    if _is_initial(words[0]):
        return None
    words = [word for word in words if not _is_initial(word)]
    if titled and len(words) == 1:
        return None  # "Dr. Schmidt": the family name
    name = _called(words)
    return name if name is not None and len(name) <= _NAME_MAX else None


def _called(words: Sequence[str]) -> str | None:
    first, last = words[0].casefold(), words[-1].casefold()
    if len(words) == 1:
        return None if first in _SURNAMES else words[0]
    if len(words) > 2 and _is_marker(words[1], first, last):
        if last in _SURNAMES or last in _SHORT_SURNAMES:
            # "Tuan Van Nguyen": given, middle, family.
            return None if first in _SURNAMES else words[0]
        return words[-1]  # "Nguyễn Văn Linh", "Le Thi Hoa"
    if first in _SURNAMES and last in _SURNAMES:
        return None  # "Nguyễn Lê": no telling which is which
    if first in _SURNAMES:
        return words[-1]  # "Trần Minh Anh"
    if last in _SURNAMES or last in _SHORT_SURNAMES:
        # Given name first: its first word that isn't a middle marker.
        given = [word for word in words[:-1] if word not in _MARKERS | _PLAIN_MARKERS]
        return given[0] if given else words[0]
    if first in _SHORT_SURNAMES:
        return None  # "Le Anh" or "Ho Wei": can't tell
    if first in _ALSO_GIVEN and last in _ALSO_GIVEN:
        return None  # "Hoàng Dương"
    if first in _ALSO_GIVEN:
        return words[-1]  # "Vũ Minh Đức"
    return words[0]


def _is_marker(word: str, first: str, last: str) -> bool:
    if word in _MARKERS:
        return True
    vietnamese = _SURNAMES | _SHORT_SURNAMES | _ALSO_GIVEN
    return word in _PLAIN_MARKERS and (first in vietnamese or last in vietnamese)


def _is_word(word: str) -> bool:
    """Letters, with inner hyphens or apostrophes ("Anne-Marie", "O'Brien")."""
    parts = word.replace("'", "-").replace("’", "-").split("-")
    return all(part.isalpha() for part in parts)


def _is_initial(word: str) -> bool:
    """ "J", "J." or "A.J."."""
    letters = word.replace(".", "")
    return letters.isalpha() and len(letters) <= 2 and (len(word) == 1 or "." in word)


def _is_cjk(char: str) -> bool:
    return any(start <= ord(char) <= end for start, end in _CJK_RANGES)
