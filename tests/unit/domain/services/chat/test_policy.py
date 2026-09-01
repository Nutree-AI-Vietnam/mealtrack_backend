from src.domain.model.chat import ChatUserContext, RetrievedKnowledgeChunk
from src.domain.services.chat.policy import (
    SentenceBuffer,
    citations_are_valid,
    filter_chunks_for_allergies,
    inspect_sentence,
    is_near_duplicate,
    label_chunks,
    nutrition_numbers_are_traceable,
    reciprocal_rank_fusion,
    request_fingerprint,
    resolve_chat_locale,
    stable_system_instructions,
)


def _context(**overrides) -> ChatUserContext:
    values = {
        "context_version": "chat_context_v1",
        "as_of": "2026-09-01T00:00:00+00:00",
        "locale": "en",
        "timezone": "UTC",
        "allergies": ["peanut"],
        "health_conditions": [],
        "dietary_preferences": [],
        "goal": "cutting",
        "tdee": 2200,
        "target_calories": 1800,
        "target_protein_g": 140,
        "target_carbs_g": 180,
        "target_fat_g": 60,
        "consumed_calories": 1150,
        "consumed_protein_g": 90,
        "consumed_carbs_g": 100,
        "consumed_fat_g": 40,
        "remaining_calories": 650,
        "remaining_protein_g": 50,
        "remaining_carbs_g": 80,
        "remaining_fat_g": 20,
        "remaining_days": 4,
    }
    values.update(overrides)
    return ChatUserContext(**values)


def test_locale_prefers_supported_request_then_profile():
    assert resolve_chat_locale("vi", "en") == "vi"
    assert resolve_chat_locale("fr", "vi") == "vi"
    assert resolve_chat_locale("fr", "de") == "en"


def test_fingerprint_changes_with_body_or_locale():
    left = request_fingerprint("hello", "en")
    assert left == request_fingerprint("hello", "en")
    assert left != request_fingerprint("hello", "vi")
    assert left != request_fingerprint("hello!", "en")


def test_stable_instructions_are_versioned_and_forbid_mutation():
    text = stable_system_instructions()
    assert "Nutree Coach" in text
    assert "Never recalculate" in text
    assert "cannot change" in text


def test_sentence_buffer_emits_on_boundary_and_flush():
    buffer = SentenceBuffer()
    assert buffer.push("Hello") == []
    assert buffer.push(". ") == ["Hello. "]
    assert buffer.push("More text") == []
    assert buffer.flush() == "More text"


def test_allergy_suggestion_is_blocked():
    decision = inspect_sentence("Try a peanut sauce tonight.", allergies=["peanut"])
    assert decision.allowed is False
    assert decision.reason == "allergy_conflict"


def test_mutation_and_leak_sentences_are_blocked():
    assert (
        inspect_sentence("I've updated your meal.", allergies=[]).reason
        == "mutation_claim"
    )
    assert (
        inspect_sentence("The context_version is chat_context_v1.", allergies=[]).reason
        == "internal_context_leak"
    )


def test_nutrition_numbers_must_come_from_context():
    context = _context()
    ok = "Nutree has 1800 calories remaining 650."
    bad = "Eat 9999 calories of protein."
    assert nutrition_numbers_are_traceable(ok, context=context, chunks=[]) is True
    assert nutrition_numbers_are_traceable(bad, context=context, chunks=[]) is False


def test_citations_must_match_retrieved_labels():
    chunks = label_chunks(
        [
            RetrievedKnowledgeChunk(
                chunk_id="c1",
                document_id="d1",
                source_key="protein-guide",
                title="Protein",
                content="Stay at the Nutree protein target.",
                locale="en",
                canonical_uri=None,
                label="",
            )
        ]
    )
    assert citations_are_valid("See [K1] for protein.", chunks) is True
    assert citations_are_valid("See [K9] for protein.", chunks) is False


def test_allergy_tagged_chunks_are_filtered():
    chunks = label_chunks(
        [
            RetrievedKnowledgeChunk(
                chunk_id="c1",
                document_id="d1",
                source_key="peanut-sauce",
                title="Peanut sauce",
                content="A peanut sauce bowl.",
                locale="en",
                canonical_uri=None,
                label="",
                safety_tags=("contains:peanut",),
            ),
            RetrievedKnowledgeChunk(
                chunk_id="c2",
                document_id="d2",
                source_key="rice-bowl",
                title="Rice bowl",
                content="A plain rice bowl.",
                locale="en",
                canonical_uri=None,
                label="",
                safety_tags=("vegetarian",),
            ),
        ]
    )
    kept = filter_chunks_for_allergies(chunks, ["peanut"])
    assert [chunk.source_key for chunk in kept] == ["rice-bowl"]


def test_reciprocal_rank_fusion_and_near_duplicate():
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "c"]])
    assert fused[0][0] == "b"
    assert is_near_duplicate(
        "Drink water with meals every day",
        "Drink water with meals every day!",
    )
    assert not is_near_duplicate("Drink water", "Log your dinner")
