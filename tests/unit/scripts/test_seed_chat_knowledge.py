import importlib.util
from pathlib import Path

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "seed_chat_knowledge.py"
)
_SPEC = importlib.util.spec_from_file_location("seed_chat_knowledge", _SCRIPT_PATH)
assert _SPEC is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MODULE)


def test_load_reviewed_english_and_vietnamese_documents() -> None:
    knowledge_dir = Path(__file__).resolve().parents[3] / "data" / "chat_knowledge"
    documents = _MODULE.load_documents(knowledge_dir)
    locales = {item["locale"] for item in documents}
    keys = {item["source_key"] for item in documents}
    assert locales == {"en", "vi"}
    assert "coach.allergies.hard-constraint.en" in keys
    assert "coach.allergies.hard-constraint.vi" in keys
    assert all(item["reviewer_id"] == "nutree-nutrition" for item in documents)


def test_chunk_content_splits_paragraphs() -> None:
    chunks = _MODULE.chunk_content("First paragraph.\n\nSecond paragraph.")
    assert chunks == ["First paragraph.", "Second paragraph."]
