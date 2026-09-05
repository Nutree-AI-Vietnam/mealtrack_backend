"""Seed reviewed Nutree Coach knowledge documents for hybrid retrieval."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.chat import (
    ChatKnowledgeChunkORM,
    ChatKnowledgeDocumentORM,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)

DEFAULT_KNOWLEDGE_DIR = (
    Path(__file__).resolve().parent.parent / "data" / "chat_knowledge"
)
REQUIRED_FIELDS = (
    "source_key",
    "title",
    "locale",
    "content_version",
    "reviewer_id",
    "content",
)


def load_documents(knowledge_dir: Path) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for path in sorted(knowledge_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"{path.name} must contain a JSON array")
        for index, item in enumerate(payload):
            if not isinstance(item, dict):
                raise ValueError(f"{path.name}[{index}] must be an object")
            missing = [
                field
                for field in REQUIRED_FIELDS
                if not str(item.get(field) or "").strip()
            ]
            if missing:
                raise ValueError(f"{path.name}[{index}] missing {missing}")
            documents.append(item)
    return documents


def chunk_content(content: str) -> list[str]:
    parts = [part.strip() for part in content.split("\n\n") if part.strip()]
    return parts or [content.strip()]


async def seed_documents(
    documents: list[dict[str, Any]], *, dry_run: bool = False
) -> int:
    if dry_run:
        return len(documents)
    async with AsyncUnitOfWork() as uow:
        session = uow.session
        for document in documents:
            await _upsert_document(session, document)
    return len(documents)


async def _upsert_document(session, document: dict[str, Any]) -> None:
    from sqlalchemy import select

    source_key = str(document["source_key"]).strip()
    content = str(document["content"]).strip()
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    now = utc_now()
    result = await session.execute(
        select(ChatKnowledgeDocumentORM).where(
            ChatKnowledgeDocumentORM.source_key == source_key
        )
    )
    row = result.scalar_one_or_none()
    chunks = chunk_content(content)
    if row is None:
        row = ChatKnowledgeDocumentORM(
            id=str(uuid.uuid4()),
            source_key=source_key,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    row.title = str(document["title"]).strip()
    row.locale = str(document["locale"]).strip()
    row.canonical_uri = str(document.get("canonical_uri") or "").strip() or None
    row.content_version = str(document["content_version"]).strip()
    row.content_sha256 = digest
    row.reviewer_id = str(document["reviewer_id"]).strip()
    row.approved_at = now
    row.safety_tags = list(document.get("safety_tags") or [])
    row.topic_tags = list(document.get("topic_tags") or [])
    row.audience_tags = list(document.get("audience_tags") or [])
    row.active = True
    row.updated_at = now
    await session.flush()
    existing = await session.execute(
        select(ChatKnowledgeChunkORM).where(ChatKnowledgeChunkORM.document_id == row.id)
    )
    for chunk in existing.scalars().all():
        await session.delete(chunk)
    for index, text in enumerate(chunks):
        session.add(
            ChatKnowledgeChunkORM(
                id=str(uuid.uuid4()),
                document_id=row.id,
                chunk_index=index,
                content=text,
                token_count=max(1, len(text.split())),
                created_at=now,
                updated_at=now,
            )
        )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        type=Path,
        default=DEFAULT_KNOWLEDGE_DIR,
        help="Directory of reviewed locale JSON files",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    documents = load_documents(args.dir)
    count = asyncio.run(seed_documents(documents, dry_run=args.dry_run))
    logger.info("seeded %s chat knowledge documents", count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
