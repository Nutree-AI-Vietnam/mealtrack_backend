"""The name a weekly recap greets the user by.

The name arrives as a display name (from the user's Google or Apple
account) and a stored first name, which is only the display name's first
word. Word order varies: Vietnamese names are written family name first
("Nguyễn Văn Linh", called Linh) or, on many accounts, given name first
("Linh Nguyễn"; Apple sign-in always joins given then family name).
Greeting someone by their family name is worse than not greeting them, so
when the order can't be told there is no greeting.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from typing import Any

# Longest part of a name (between hyphens) that still fits one line of the
# intro headline; a longer one would break mid-word.
_PART_MAX = 12
_NAME_MAX = 24
# Chinese and Japanese names are used whole; this many characters fit.
_CJK_MAX = 6

# Only ever family names, with and without diacritics.
_SURNAMES = frozenset(
    word.casefold()
    for word in (
        "Nguyễn Trần Lê Phạm Huỳnh Phan Võ Đặng Bùi Đỗ Hồ Ngô Đoàn Trịnh "
        "Trương Đinh Lương Phùng Quách Lưu Tạ "
        "Nguyen Tran Le Pham Huynh Vo Bui Do Ho Ngo Doan Quach Luu Ta"
    ).split()
)
# Family names that are as often given names (unaccented, several of the
# names above are also given names: Trinh, Truong, Dang...).
_ALSO_GIVEN = frozenset(
    word.casefold()
    for word in (
        "Hoàng Vũ Dương Lý Đào Vương "
        "Hoang Vu Duong Ly Dao Vuong Dang Dinh Luong Truong Trinh Phung"
    ).split()
)
# Vietnamese middle-name markers: never what someone is called.
_MIDDLE = frozenset({"văn", "thị", "van", "thi"})
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
    """The words of a name, without honorifics; None when it isn't a name."""
    words = text.split()
    while len(words) > 1 and words[0].casefold().rstrip(".") in _HONORIFICS:
        words.pop(0)
    if not words:
        return None
    cjk = [word for word in words if all(_is_cjk(char) for char in word)]
    if cjk:
        return ["".join(cjk)]
    if not all(_is_word(word) or _is_initial(word) for word in words):
        return None
    return words


def _greeting(words: Sequence[str]) -> str | None:
    """The word someone with this name is called, when it can be told."""
    if _is_initial(words[0]):
        return None
    words = [word for word in words if not _is_initial(word)]
    if len(words) > 1 and words[0].casefold() in _MIDDLE:
        words = words[1:]  # "Thị Mai Nguyễn": the given name came first
    name = _called(words)
    return name if name is not None and _fits(name) else None


def _called(words: Sequence[str]) -> str | None:
    if len(words) == 1:
        return words[0]
    if len(words) > 2 and words[1].casefold() in _MIDDLE:
        return words[-1]  # "Nguyễn Văn Linh"
    first, last = words[0].casefold(), words[-1].casefold()
    if first in _SURNAMES and last in _SURNAMES:
        return None  # "Nguyễn Lê": no telling which is which
    if first in _SURNAMES:
        return words[-1]  # "Trần Minh Anh"
    if last in _SURNAMES:
        # Given name first. A Vietnamese given name goes by its last word
        # ("Hoàng Nam Nguyễn" is Nam); other names by their first.
        return words[-2] if _vietnamese(words) else words[0]
    if first in _ALSO_GIVEN and last in _ALSO_GIVEN:
        return None  # "Hoàng Dương"
    if first in _ALSO_GIVEN:
        return words[-1]  # "Vũ Minh Đức"
    return words[0]


def _fits(name: str) -> bool:
    if _is_cjk(name[0]):
        return len(name) <= _CJK_MAX
    return len(name) <= _NAME_MAX and all(
        len(part) <= _PART_MAX for part in name.split("-")
    )


def _is_word(word: str) -> bool:
    """Letters, with inner hyphens or apostrophes ("Anne-Marie", "O'Brien")."""
    parts = word.replace("'", "-").replace("’", "-").split("-")
    return all(part.isalpha() for part in parts)


def _is_initial(word: str) -> bool:
    """ "J", "J." or "A.J."."""
    letters = word.replace(".", "")
    return letters.isalpha() and len(letters) <= 2 and (len(word) == 1 or "." in word)


def _vietnamese(words: Sequence[str]) -> bool:
    """Whether the name is written with Vietnamese letters."""
    return any(
        char in "đĐ" or unicodedata.decomposition(char) for char in "".join(words)
    )


def _is_cjk(char: str) -> bool:
    return any(start <= ord(char) <= end for start, end in _CJK_RANGES)
