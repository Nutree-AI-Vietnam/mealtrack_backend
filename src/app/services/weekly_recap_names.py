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

import re
import unicodedata
from collections.abc import Sequence
from itertools import pairwise
from typing import Any

# Longest name that still fits one line of the intro headline at 50pt; a
# longer one would shrink the title or break mid-word.
_NAME_MAX = 12
# Chinese, Japanese and Korean names are used whole; this many characters fit.
_CJK_MAX = 6


def _folded(words: str) -> frozenset[str]:
    return frozenset(word.casefold() for word in words.split())


# Vietnamese family names that are only ever family names, with and without
# diacritics (an unaccented form that is also a given name or a word is in
# one of the lists below instead).
_SURNAMES = _folded(
    "Nguyễn Trần Lê Phạm Huỳnh Phan Võ Đặng Bùi Đỗ Hồ Ngô Đoàn Trịnh "
    "Trương Đinh Lương Phùng Quách Lưu Tạ Cao Tô Lại Tăng Thân Triệu Từ "
    "Tôn Mạc Hứa Đàm Tống Bạch Khúc Lục Nông Thạch Hầu Doãn Phí Tiêu Âu "
    "Lò Bế Sầm Ông Chu "
    "Nguyen Tran Pham Huynh Bui Ngo Doan Quach Luu Cao Trieu Khuc Nong "
    "Thach Tieu Chu"
)
# Vietnamese family names that are as often given names ("Hoàng Nguyễn" is
# Hoàng; "Hoàng Văn Nam" is Nam).
_ALSO_GIVEN = _folded(
    "Hoàng Vũ Dương Lý Đào Vương Hà Lâm Mai Châu Thái Giang Ninh Kiều "
    "Diệp Quan "
    "Hoang Vu Duong Ly Dao Vuong Dang Dinh Luong Truong Trinh Phung Ha Lam "
    "Chau Thai Kieu Diep Tu Hau Phi"
)
# Unaccented Vietnamese family names short enough to be something else (a
# word, a Western name): a family name at the end of a name, and at its
# start only when the rest of the name is plainly Vietnamese.
_SHORT_SURNAMES = _folded(
    "le do ho vo ta to la lai tang than ton dam tong hua bach au lo be ong"
)
# Romanized Korean, Chinese and Japanese family names. Written first ("Kim
# Min-jun", "Zhang Wei", "Yamada Taro") or last ("Taro Yamada") about as
# often, so a name that starts with one gets no greeting.
_EAST_SURNAMES = _folded(
    "kim lee park choi jung jeong kang cho yoon yun jang lim han oh seo "
    "shin kwon hwang ahn song hong yoo "
    "wang li zhang liu chen yang huang zhao zhou wu xu sun ma zhu hu guo "
    "he lin gao luo zheng liang xie tang feng deng peng zeng xiao tian "
    "dong pan yuan cai jiang yu du ye cheng wei su lu ding ren shen yao "
    "cui zhong tan fan jin shi liao jia xia fu fang bai zou meng xiong qin "
    "qiu yin xue yan duan lei hou long tao gu mao hao gong shao wan qian "
    "dai kong xiang chan wong cheung lau leung ng tsang chow yip tse fung "
    "kwok mak yeung hui siu "
    "sato suzuki takahashi tanaka watanabe ito yamamoto nakamura kobayashi "
    "kato yoshida yamada sasaki yamaguchi matsumoto inoue kimura hayashi "
    "shimizu yamazaki mori ikeda hashimoto yamashita ishikawa nakajima "
    "maeda fujita ogawa goto okada hasegawa murakami kondo ishii saito "
    "sakamoto endo aoki fujii nishimura fukuda ota miura fujiwara okamoto "
    "matsuda nakagawa"
)
# Vietnamese middle-name markers, never what someone is called. The accented
# forms only occur in Vietnamese names; the plain ones also start Dutch and
# other family names ("Van Gogh") and are given names ("Van" for Vân), so
# they count only next to a Vietnamese family name.
_MARKERS = _folded("Văn Thị")
_PLAIN_MARKERS = _folded("Van Thi")
_HONORIFICS = _folded("dr mr mrs ms mx prof sir mme mlle herr frau sr sra srta dra")
# Hiragana and katakana, CJK ideographs, and Hangul syllables.
_CJK_RANGES = ((0x3040, 0x30FF), (0x3400, 0x9FFF), (0xAC00, 0xD7AF))
# Combining marks only Vietnamese puts on Latin letters: the hook above, the
# dot below and the horn (ơ, ư). A tone on a circumflex vowel (ấ, ề, ổ)
# is Vietnamese too.
_VIETNAMESE_MARKS = frozenset("\u0309\u0323\u031b")
# One Vietnamese syllable, without its marks: every Vietnamese name word is
# one ("Tuấn", "Nguyễn"), and "Smith" or "Tanaka" is not.
_SYLLABLE = re.compile(
    r"(ngh|ng|nh|ch|gh|gi|kh|ph|qu|th|tr|[bcdghklmnprstvx])?"
    r"[aeiouy]{1,3}(ch|ng|nh|[cmnpt])?"
)
# A token starting with one of these opens a nickname or a note, "(Tony)".
_OPENERS = '([{<"“‘«'

# How surely a word is a family name.
_NOT, _MAYBE, _OFTEN, _ONLY = range(4)


def recap_first_name(user: Any) -> str | None:
    """The name to greet ``user`` by, or None.

    Reads the display name, which keeps the order the user's name was
    written in. The first name stands in only when the display name is
    missing or is a single word that is not a name (a handle, an e-mail):
    it is that display name's first word, so it adds nothing to a longer
    one.
    """
    display = unicodedata.normalize("NFC", str(getattr(user, "display_name", "") or ""))
    words = _name_words(display)
    if words is not None:
        return _greeting(words)
    if len(display.split()) > 1:
        return None
    first = unicodedata.normalize("NFC", str(getattr(user, "first_name", "") or ""))
    words = _name_words(first)
    return _greeting(words) if words is not None else None


def _name_words(text: str) -> list[str] | None:
    """The words of a name; None when there is no name or it isn't one.

    Decorations with no letters (emoji, symbols) are dropped, and the name
    ends at one that follows it or at a bracket: "Nguyễn Thu Hà | Nutree",
    "Hoàng Văn Nam (Tony)". A name written all in lower or upper case is
    capitalized, as a headline writes it.
    """
    words: list[str] = []
    cjk: list[str] = []
    for token in text.split():
        if token.startswith(tuple(_OPENERS)) or not any(
            char.isalpha() for char in token
        ):
            if words or cjk:
                break
            continue
        if all(_is_cjk(char) for char in token):
            cjk.append(token)
            continue
        word = token if _is_initial(token) else token.rstrip(".")
        if not (_is_word(word) or _is_initial(word)):
            if cjk:
                continue  # "山田太郎 Taro"
            return None
        words.append(word)
    if cjk:
        return ["".join(cjk)]
    if not words:
        return None
    if all(word.islower() for word in words) or all(word.isupper() for word in words):
        words = [word.title() for word in words]
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
    if name is None or len(name) > _NAME_MAX:
        return None
    if name.casefold() in _SURNAMES | _SHORT_SURNAMES | _MARKERS:
        return None
    return name


def _called(words: Sequence[str]) -> str | None:
    markers = [_is_marker(word, words) for word in words]
    # Apple joins given then family name, and many write "Thị Lan" as the
    # given name: a leading marker is followed by what the person is called.
    # A plain "Van" or "Thi" can be the given name itself ("Van Nguyen").
    lead = 0
    while lead < len(words) - 1 and markers[lead]:
        if words[lead].casefold() in _PLAIN_MARKERS and len(words) - lead < 3:
            break
        lead += 1
    words, markers = words[lead:], markers[lead:]
    if len(words) == 1:
        return words[0]
    vietnamese = any(markers) or _looks_vietnamese(words)
    first = _family_rank(words[0], vietnamese)
    last = _family_rank(words[-1], vietnamese)
    if first == last:
        # Neither end is more surely the family name: a Western name, or no
        # telling ("Nguyễn Lê", "Hoàng Dương", an unknown Vietnamese name).
        return words[0] if first == _NOT and not vietnamese else None
    if first > last:
        # Family name first, unless the first word may as well be a given
        # name ("Le Anh", "Kim Min-jun"). A family name that is often a
        # given name is followed by a Vietnamese given name ("Hoàng Minh
        # Đức"); before another family name it is the given name ("Mai
        # Smith", "Mai Tanaka").
        if first == _MAYBE:
            return None
        if first == _OFTEN and not _is_syllable(words[-1]):
            return words[0]
        return None if first == _OFTEN and last == _MAYBE else words[-1]
    # Given name first: its first word that isn't a middle marker.
    given = [
        word
        for word, marker in zip(words[:-1], markers[:-1], strict=True)
        if not marker
    ]
    return given[0] if given else words[0]


def _family_rank(word: str, vietnamese: bool) -> int:
    folded = word.casefold()
    if folded in _SURNAMES:
        return _ONLY
    if folded in _ALSO_GIVEN or (vietnamese and folded in _SHORT_SURNAMES):
        return _OFTEN
    if folded in _SHORT_SURNAMES or folded in _EAST_SURNAMES:
        return _MAYBE
    return _NOT


def _looks_vietnamese(words: Sequence[str]) -> bool:
    """A listed Vietnamese family name or a mark only Vietnamese uses."""
    if any(word.casefold() in _SURNAMES | _ALSO_GIVEN for word in words):
        return True
    text = unicodedata.normalize("NFD", " ".join(words).casefold())
    if "đ" in text or any(char in _VIETNAMESE_MARKS for char in text):
        return True
    return any(
        unicodedata.combining(a) and unicodedata.combining(b) for a, b in pairwise(text)
    )


def _is_syllable(word: str) -> bool:
    bare = unicodedata.normalize("NFD", word.casefold().replace("đ", "d"))
    bare = "".join(char for char in bare if not unicodedata.combining(char))
    return _SYLLABLE.fullmatch(bare) is not None


def _is_marker(word: str, words: Sequence[str]) -> bool:
    folded = word.casefold()
    if folded in _MARKERS:
        return True
    vietnamese = _SURNAMES | _SHORT_SURNAMES | _ALSO_GIVEN
    ends = (words[0].casefold(), words[-1].casefold())
    return folded in _PLAIN_MARKERS and any(end in vietnamese for end in ends)


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
