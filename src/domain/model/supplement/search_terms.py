"""Vietnamese names people type for what supplement labels print in English.

The catalog mostly carries English label names ("Iron 18 mg", "Fish Oil"),
while people search in Vietnamese ("sắt", "dầu cá"). Each known Vietnamese
term in a folded query also matches its English label words.
"""

from __future__ import annotations

from dataclasses import dataclass

# Folded Vietnamese words -> alternative English label words.
_SYNONYMS: dict[tuple[str, ...], tuple[tuple[str, ...], ...]] = {
    ("sat",): (("iron",),),
    ("canxi",): (("calcium",),),
    ("kem",): (("zinc",),),
    ("magie",): (("magnesium",),),
    ("kali",): (("potassium",),),
    ("selen",): (("selenium",),),
    ("iot",): (("iodine",),),
    ("i", "ot"): (("iodine",),),
    ("dau", "ca"): (("fish", "oil"), ("omega",)),
    ("dau", "gan", "ca"): (("cod", "liver"),),
    ("axit", "folic"): (("folic",), ("folate",)),
    ("men", "vi", "sinh"): (("probiotic",),),
    ("vitamin", "tong", "hop"): (("multivitamin",), ("multi",)),
    ("tao", "xoan"): (("spirulina",),),
    ("sua", "ong", "chua"): (("royal", "jelly"),),
    ("hoa", "anh", "thao"): (("evening", "primrose"),),
    ("nghe",): (("turmeric",), ("curcumin",)),
    ("keo", "ong"): (("propolis",),),
    ("dau", "hat", "lanh"): (("flaxseed",), ("flax",)),
}
_LONGEST = max(len(phrase) for phrase in _SYNONYMS)


@dataclass(frozen=True)
class SearchTerm:
    """One thing the product name must contain.

    ``typed`` is what the person wrote. A known Vietnamese term matches only
    as whole words (so "sat" never hits "Satiety") or as any English
    ``alternatives``; any other word matches as before.
    """

    typed: tuple[str, ...]
    alternatives: tuple[tuple[str, ...], ...] = ()

    @property
    def translated(self) -> bool:
        return bool(self.alternatives)


def supplement_search_terms(words: list[str]) -> list[SearchTerm]:
    """Group folded query words into terms, longest Vietnamese phrase first."""
    terms: list[SearchTerm] = []
    index = 0
    while index < len(words):
        for size in range(min(_LONGEST, len(words) - index), 0, -1):
            phrase = tuple(words[index : index + size])
            if phrase in _SYNONYMS:
                terms.append(SearchTerm(phrase, _SYNONYMS[phrase]))
                index += size
                break
        else:
            terms.append(SearchTerm((words[index],)))
            index += 1
    return terms
