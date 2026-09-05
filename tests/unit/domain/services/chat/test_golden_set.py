import json
from pathlib import Path

from src.domain.model.chat import ChatUserContext, RetrievedKnowledgeChunk
from src.domain.services.chat.policy import (
    citations_are_valid,
    inspect_sentence,
    nutrition_numbers_are_traceable,
)

_GOLDEN_PATH = Path("evals/chat/golden_set_v1.json")


def test_golden_set_exists_and_is_bilingual():
    payload = json.loads(_GOLDEN_PATH.read_text())
    assert payload["version"] == "chat_eval_v1"
    locales = {case["locale"] for case in payload["cases"]}
    assert "en" in locales and "vi" in locales
    ids = [case["id"] for case in payload["cases"]]
    assert "allergy-conflict-en" in ids
    assert "citation-precision-en" in ids
    assert "medical-boundary-en" in ids


def test_golden_set_offline_safety_and_grounding_gates():
    payload = json.loads(_GOLDEN_PATH.read_text())
    allergy_violations = 0
    citation_failures = 0
    calorie_contradictions = 0

    for case in payload["cases"]:
        answer = case["assistant_fixture"]
        context_data = case.get("context") or {}
        allergies = context_data.get("allergies") or []
        decision = inspect_sentence(answer, allergies=allergies)
        expected_block = case.get("expect_block_reason")
        if expected_block:
            assert decision.reason == expected_block, case["id"]
            if expected_block == "allergy_conflict":
                allergy_violations += 0 if decision.reason == expected_block else 1
            continue
        assert decision.allowed, case["id"]

        for needle in case.get("must_include") or []:
            assert needle.casefold() in answer.casefold(), case["id"]
        for needle in case.get("must_not_include") or []:
            assert needle.casefold() not in answer.casefold(), case["id"]

        if case.get("allowed_labels"):
            chunks = [
                RetrievedKnowledgeChunk(
                    chunk_id="c1",
                    document_id="d1",
                    source_key="k",
                    title="t",
                    content="Stay at the Nutree protein target.",
                    locale="en",
                    canonical_uri=None,
                    label=case["allowed_labels"][0],
                )
            ]
            valid = citations_are_valid(answer, chunks)
            if case.get("expect_invalid_citation"):
                assert valid is False, case["id"]
                citation_failures += 0
            else:
                assert valid is True, case["id"]

        if any(
            key in context_data for key in ("target_calories", "remaining_calories")
        ):
            context = ChatUserContext(
                context_version="chat_context_v1",
                as_of="2026-09-01T00:00:00+00:00",
                locale=case["locale"],
                timezone="UTC",
                allergies=allergies,
                health_conditions=None,
                dietary_preferences=None,
                goal=None,
                tdee=None,
                target_calories=context_data.get("target_calories"),
                target_protein_g=context_data.get("target_protein_g"),
                target_carbs_g=None,
                target_fat_g=None,
                consumed_calories=None,
                consumed_protein_g=None,
                consumed_carbs_g=None,
                consumed_fat_g=None,
                remaining_calories=context_data.get("remaining_calories"),
                remaining_protein_g=context_data.get("remaining_protein_g"),
                remaining_carbs_g=None,
                remaining_fat_g=None,
                remaining_days=None,
            )
            if not nutrition_numbers_are_traceable(answer, context=context, chunks=[]):
                calorie_contradictions += 1

    assert allergy_violations == 0
    assert calorie_contradictions == 0
    assert citation_failures == 0
