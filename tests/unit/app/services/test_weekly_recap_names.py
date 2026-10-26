"""The name a weekly recap greets the user by."""

import unicodedata
from types import SimpleNamespace

import pytest

from src.app.services.weekly_recap_names import recap_first_name


def _greet(display_name, first_name=None):
    return recap_first_name(
        SimpleNamespace(display_name=display_name, first_name=first_name)
    )


@pytest.mark.parametrize(
    ("display_name", "expected"),
    [
        # Family name first, the Vietnamese way.
        ("Nguyễn Văn Linh", "Linh"),
        ("Tran Minh Anh", "Anh"),
        ("Le Thi Hoa", "Hoa"),
        ("Do Thi Lan", "Lan"),
        ("Phạm Thị Lý", "Lý"),
        ("Hoàng Văn Nam", "Nam"),
        ("Nguyễn Hoàng", "Hoàng"),
        ("Trần Vũ", "Vũ"),
        ("Nguyen Trinh", "Trinh"),
        ("Vu Minh Duc", "Duc"),
        ("Hà Anh Tuấn", "Tuấn"),
        ("Cao Thái Sơn", "Sơn"),
        ("Mai Phương Thúy", "Thúy"),
        ("Kiều Minh Tuấn", "Tuấn"),
        ("Tô Minh Châu", "Châu"),
        ("Lại Thanh Sâm", "Sâm"),
        ("Ha Anh Tuan", "Tuan"),
        ("Cao Thai Son", "Son"),
        ("Chau Van Thanh", "Thanh"),
        ("Mai Anh", "Anh"),
        ("Nguyễn Văn Nam.", "Nam"),
        # Given name first (Apple sign-in, many Google accounts).
        ("Linh Nguyen", "Linh"),
        ("Hoàng Nguyễn", "Hoàng"),
        ("Hoàng Nam Nguyễn", "Hoàng"),
        ("Thị Mai Nguyễn", "Mai"),
        ("Tuan Van Nguyen", "Tuan"),
        ("Mai Thi Nguyen", "Mai"),
        ("Linh Thị Nguyễn", "Linh"),
        ("Tuấn Văn Trần", "Tuấn"),
        ("Hoang Van Nguyen", "Hoang"),
        ("Dương Thị Lê", "Dương"),
        ("Mai Thi Ngoc Nguyen", "Mai"),
        ("Van Nguyen", "Van"),
        ("Vũ Trần", "Vũ"),
        ("Dương Lê", "Dương"),
        ("Trinh Nguyen", "Trinh"),
        ("Ly Tran", "Ly"),
        ("Kevin Minh Tran", "Kevin"),
        ("François Xavier Le", "François"),
        ("Céline Anne Pham", "Céline"),
        # A family name that is often a given name, at the end.
        ("Lan Thị Hoàng", "Lan"),
        ("Tuấn Văn Vũ", "Tuấn"),
        ("Long Văn Đào", "Long"),
        ("Tuan Van Dang", "Tuan"),
        ("Hung Van Truong", "Hung"),
        # Apple joins given then family name, and the given name may start
        # with a middle marker.
        ("Thị Lan Hoàng", "Lan"),
        ("Văn Minh Vũ", "Minh"),
        ("Thi Lan Hoang", "Lan"),
        ("Thị Mai", "Mai"),
        # Written all in capitals, as on an ID card.
        ("NGUYỄN VĂN LINH", "Linh"),
        ("THỊ MAI NGUYỄN", "Mai"),
        ("VAN TUAN NGUYEN", "Tuan"),
        # A note after the name ends it.
        ("Lê Thị Hoa – Kế toán", "Hoa"),
        ("Nguyễn Thu Hà | Nutree", "Hà"),
        ("Hoàng Văn Nam (Tony)", "Nam"),
        # No telling which word is the family name.
        ("Nguyễn Lê", None),
        ("Nguyễn Văn Trần", None),
        ("Hoang Duong", None),
        ("Le Anh", None),
        ("Nguyễn", None),
        ("Châu Ngọc Hà", None),
        ("Mai Thị Dương", None),
        ("Thừa Dụ", None),  # Vietnamese, but neither word is a known family name
        # Korean, Chinese and Japanese names are written either way round.
        ("Kim Min-jun", None),
        ("Zhang Wei", None),
        ("Yamada Taro", None),
        ("Min-jun Kim", "Min-jun"),
        ("Taro Yamada", "Taro"),
        # A Vietnamese given name before a family name from elsewhere.
        ("Mai Smith", "Mai"),
        ("Mai Tanaka", "Mai"),
        ("Ha Kim", None),
        # Other names.
        ("Anna Schmidt", "Anna"),
        ("Dr. Anna Schmidt", "Anna"),
        ("Dr. Schmidt", None),
        ("Mrs. Nguyen", None),
        ("Vincent Van Gogh", "Vincent"),
        ("Jan van Dijk", "Jan"),
        ("Anne-Marie  Dupont", "Anne-Marie"),
        ("O'Brien", "O'Brien"),
        ("Đức", "Đức"),
        ("linh nguyen", "Linh"),
        ("Nguyễn Văn Linh 🌸", "Linh"),
        ("Bartholomew Smith", "Bartholomew"),
        ("Jean-Christophe Martin", None),  # too long for the headline
        ("Lakshminarayanan Iyer", None),
        ("J. R. Smith", None),  # an initial is not a name to greet by
        ("John F. Kennedy", "John"),
        # Chinese, Japanese and Korean names are used whole.
        ("王小明", "王小明"),
        ("山田 太郎", "山田太郎"),
        ("山田太郎 Taro", "山田太郎"),
        ("김민준", "김민준"),
    ],
)
def test_recap_first_name(display_name, expected):
    assert _greet(display_name) == expected


def test_decomposed_vietnamese_reads_like_composed():
    assert _greet(unicodedata.normalize("NFD", "Nguyễn Văn Linh")) == "Linh"


@pytest.mark.parametrize(
    ("display_name", "first_name", "expected"),
    [
        (None, "Linh", "Linh"),
        ("", "Linh", "Linh"),
        ("alex.nguyen92", "Alex", "Alex"),  # a handle is not a name
        ("alex@nutree.ai", None, None),
        ("Linh 🌸", "Linh", "Linh"),
        # The stored first name is the display name's first word, so it
        # must not stand in for a name whose order can't be told.
        ("Nguyễn Lê", "Nguyễn", None),
        ("Hoàng Văn Nam 99x", "Hoàng", None),
        ("🌸", "Linh", "Linh"),
        (None, None, None),
    ],
)
def test_the_first_name_stands_in_only_for_a_missing_name(
    display_name, first_name, expected
):
    assert _greet(display_name, first_name) == expected
