from src.domain.model.chat import RetrievedKnowledgeChunk
from src.infra.adapters.chat_knowledge_retrieval_adapter import _with_fused_score


def test_fused_chunk_keeps_safety_tags() -> None:
    chunk = RetrievedKnowledgeChunk(
        chunk_id="c1",
        document_id="d1",
        source_key="peanut-sauce",
        title="Peanut sauce",
        content="A peanut sauce bowl.",
        locale="en",
        canonical_uri=None,
        label="",
        vector_score=0.9,
        fts_rank=0.2,
        safety_tags=("contains:peanut",),
    )
    fused = _with_fused_score(chunk, 0.42)
    assert fused.safety_tags == ("contains:peanut",)
    assert fused.fused_score == 0.42
    assert fused.vector_score == 0.9
