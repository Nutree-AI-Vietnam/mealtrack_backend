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
        ("Nguyễn Hoàng", "Hoàng"),
        ("Trần Vũ", "Vũ"),
        ("Nguyen Trinh", "Trinh"),
        ("Vu Minh Duc", "Duc"),
        # Given name first (Apple sign-in, many Google accounts).
        ("Linh Nguyen", "Linh"),
        ("Hoàng Nguyễn", "Hoàng"),
        ("Hoàng Nam Nguyễn", "Nam"),
        ("Thị Mai Nguyễn", "Mai"),
        ("Vũ Trần", "Vũ"),
        ("Dương Lê", "Dương"),
        ("Trinh Nguyen", "Trinh"),
        ("Ly Tran", "Ly"),
        ("Kevin Minh Tran", "Kevin"),
        # No telling which word is the family name.
        ("Nguyễn Lê", None),
        ("Hoang Duong", None),
        # Other names.
        ("Mai Anh", "Mai"),
        ("Anna Schmidt", "Anna"),
        ("Dr. Anna Schmidt", "Anna"),
        ("Anne-Marie  Dupont", "Anne-Marie"),
        ("O'Brien", "O'Brien"),
        ("Đức", "Đức"),
        ("Bartholomew-Maximilian Smith", "Bartholomew-Maximilian"),
        ("Lakshminarayanan Iyer", None),  # breaks mid-word in the headline
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
        (None, None, None),
    ],
)
def test_the_first_name_stands_in_only_for_a_missing_name(
    display_name, first_name, expected
):
    assert _greet(display_name, first_name) == expected
