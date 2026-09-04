import pytest

from src.app.services.chat_intent_classifier import ChatIntentClassifier
from src.domain.services.chat.intent_classification import fold_intent_text
from src.domain.services.chat.topic_scope import SCOPE_IN, SCOPE_OUT


class _AxisEmbedding:
    """Maps known stems onto four axes so cosine can pick an intent in tests."""

    async def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(text) for text in texts]

    def _vec(self, text: str) -> list[float]:
        folded = fold_intent_text(text)
        return [
            1.0 if _has(folded, ("supper", "dinner", "eat next", "an gi")) else 0.02,
            1.0
            if _has(folded, ("left", "bao nhieu", "eaten", "calorie cap"))
            else 0.02,
            1.0 if _has(folded, ("on track", "day going", "the nao")) else 0.02,
            1.0
            if _has(folded, ("can't", "cannot", "log this", "doi muc tieu"))
            else 0.02,
        ]


class _ScopeEmbedding:
    async def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(text) for text in texts]

    def _vec(self, text: str) -> list[float]:
        folded = fold_intent_text(text)
        return [
            1.0
            if _has(
                folded,
                ("protein", "rice", "water", "tdee", "logged", "macro", "fiber"),
            )
            else 0.05,
            1.0
            if _has(
                folded,
                (
                    "python",
                    "javascript",
                    "kubernetes",
                    "weather",
                    "stock",
                    "debug",
                    "rust",
                    "compiler",
                ),
            )
            else 0.05,
        ]


def _has(folded: str, stems: tuple[str, ...]) -> bool:
    return any(stem in folded for stem in stems)


@pytest.mark.asyncio
async def test_phrase_wins_before_embeddings() -> None:
    classifier = ChatIntentClassifier(_AxisEmbedding())
    decision = await classifier.classify("What's for dinner?")
    assert decision.intent == "next_meal"
    assert decision.source == "phrase"


@pytest.mark.asyncio
async def test_embedding_classifies_unlisted_paraphrase() -> None:
    classifier = ChatIntentClassifier(_AxisEmbedding())
    decision = await classifier.classify("got a supper pick?")
    assert decision.intent == "next_meal"
    assert decision.source == "embedding"


@pytest.mark.asyncio
async def test_unrelated_text_stays_none() -> None:
    classifier = ChatIntentClassifier(_AxisEmbedding())
    decision = await classifier.classify("Cite protein guidance")
    assert decision.intent is None
    assert decision.source == "none"


@pytest.mark.asyncio
async def test_scope_phrase_rejects_coding() -> None:
    classifier = ChatIntentClassifier(_AxisEmbedding())
    decision = await classifier.classify_scope("Write a Python function")
    assert decision.scope == SCOPE_OUT
    assert decision.source == "phrase"


@pytest.mark.asyncio
async def test_scope_embedding_rejects_paraphrase() -> None:
    classifier = ChatIntentClassifier(_ScopeEmbedding())
    decision = await classifier.classify_scope("fix my rust compiler panic")
    assert decision.scope == SCOPE_OUT
    assert decision.source == "embedding"


@pytest.mark.asyncio
async def test_scope_defaults_in_for_nutrition_paraphrase() -> None:
    classifier = ChatIntentClassifier(_ScopeEmbedding())
    decision = await classifier.classify_scope("is brown rice fine while cutting?")
    assert decision.scope == SCOPE_IN


@pytest.mark.asyncio
async def test_reuses_query_embedding() -> None:
    class _CountEmbed(_AxisEmbedding):
        def __init__(self) -> None:
            self.calls = 0

        async def embed_query(self, text: str) -> list[float]:
            self.calls += 1
            return await super().embed_query(text)

    embedding = _CountEmbed()
    classifier = ChatIntentClassifier(embedding)
    query = embedding._vec("got a supper pick?")
    await classifier.classify("got a supper pick?", query_embedding=query)
    assert embedding.calls == 0
