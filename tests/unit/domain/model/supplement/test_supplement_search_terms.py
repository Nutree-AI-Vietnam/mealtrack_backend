"""Vietnamese query words grouped into terms with their English label words."""

from src.domain.model.supplement.search_terms import SearchTerm, supplement_search_terms


def test_known_vietnamese_terms_carry_their_english_label_words():
    assert supplement_search_terms(["sat"]) == [SearchTerm(("sat",), (("iron",),))]
    assert supplement_search_terms(["dau", "ca", "blackmores"]) == [
        SearchTerm(("dau", "ca"), (("fish", "oil"), ("omega",))),
        SearchTerm(("blackmores",)),
    ]


def test_the_longest_phrase_wins():
    terms = supplement_search_terms(["dau", "gan", "ca"])

    assert terms == [SearchTerm(("dau", "gan", "ca"), (("cod", "liver"),))]


def test_other_words_pass_through_unchanged():
    terms = supplement_search_terms(["vitamin", "c", "1000"])

    assert [term.typed for term in terms] == [("vitamin",), ("c",), ("1000",)]
    assert not any(term.translated for term in terms)
