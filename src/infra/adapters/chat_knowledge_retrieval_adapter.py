"""Hybrid full-text + pgvector retrieval over reviewed Nutree knowledge."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.chat import (
    CHAT_RETRIEVAL_MAX_CHUNKS,
    RetrievedKnowledgeChunk,
)
from src.domain.ports.chat_knowledge_retrieval_port import ChatKnowledgeRetrievalPort
from src.domain.services.chat.policy import (
    filter_chunks_for_allergies,
    is_near_duplicate,
    label_chunks,
    reciprocal_rank_fusion,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.chat import (
    ChatKnowledgeChunkORM,
    ChatKnowledgeDocumentORM,
)

_VECTOR_CANDIDATES = 20
_FTS_CANDIDATES = 20
_MIN_VECTOR_SCORE = 0.25
_MIN_FTS_RANK = 0.05
_MIN_FUSED_SCORE = 0.012


class ChatKnowledgeRetrievalAdapter(ChatKnowledgeRetrievalPort):
    def __init__(self, uow_factory: type) -> None:
        self._uow_factory = uow_factory

    async def retrieve(
        self,
        *,
        query: str,
        query_embedding: list[float] | None,
        locale: str,
        allergies: list[str],
        limit: int,
    ) -> list[RetrievedKnowledgeChunk]:
        async with self._uow_factory() as uow:
            chunks = await self._retrieve_with_session(
                uow.session,
                query=query,
                query_embedding=query_embedding,
                locale=locale,
                limit=limit,
            )
        return filter_chunks_for_allergies(chunks, allergies)

    async def _retrieve_with_session(
        self,
        session: AsyncSession,
        *,
        query: str,
        query_embedding: list[float] | None,
        locale: str,
        limit: int,
    ) -> list[RetrievedKnowledgeChunk]:
        now = utc_now()
        cap = min(max(limit, 1), CHAT_RETRIEVAL_MAX_CHUNKS)

        fts_hits = await self._fts_search(session, query, locale, now)
        vector_hits = (
            await self._vector_search(session, query_embedding, locale, now)
            if query_embedding
            else []
        )
        if not fts_hits and not vector_hits:
            return []

        fused_ids = [
            item_id
            for item_id, score in reciprocal_rank_fusion(
                [
                    [chunk.chunk_id for chunk in vector_hits],
                    [chunk.chunk_id for chunk in fts_hits],
                ]
            )
            if score >= _MIN_FUSED_SCORE
        ]
        by_id = {chunk.chunk_id: chunk for chunk in [*vector_hits, *fts_hits]}
        fused: list[RetrievedKnowledgeChunk] = []
        scores = dict(
            reciprocal_rank_fusion(
                [
                    [chunk.chunk_id for chunk in vector_hits],
                    [chunk.chunk_id for chunk in fts_hits],
                ]
            )
        )
        for chunk_id in fused_ids:
            chunk = by_id.get(chunk_id)
            if chunk is None:
                continue
            if not _passes_threshold(chunk):
                continue
            fused.append(
                RetrievedKnowledgeChunk(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    source_key=chunk.source_key,
                    title=chunk.title,
                    content=chunk.content,
                    locale=chunk.locale,
                    canonical_uri=chunk.canonical_uri,
                    label=chunk.label,
                    vector_score=chunk.vector_score,
                    fts_rank=chunk.fts_rank,
                    fused_score=scores.get(chunk_id, 0.0),
                )
            )

        deduped: list[RetrievedKnowledgeChunk] = []
        for chunk in fused:
            if any(is_near_duplicate(chunk.content, kept.content) for kept in deduped):
                continue
            deduped.append(chunk)
            if len(deduped) >= cap:
                break
        return label_chunks(deduped)

    async def _eligible_documents(self, locale: str, now: datetime):
        return (
            ChatKnowledgeDocumentORM.active.is_(True),
            ChatKnowledgeDocumentORM.locale == locale,
            or_(
                ChatKnowledgeDocumentORM.expires_at.is_(None),
                ChatKnowledgeDocumentORM.expires_at > now,
            ),
        )

    async def _fts_search(
        self, session: AsyncSession, query: str, locale: str, now: datetime
    ) -> list[RetrievedKnowledgeChunk]:
        tsq = func.plainto_tsquery("simple", query)
        rank = func.ts_rank_cd(ChatKnowledgeChunkORM.tsv, tsq)
        stmt = (
            select(
                ChatKnowledgeChunkORM,
                ChatKnowledgeDocumentORM,
                rank.label("rank"),
            )
            .join(
                ChatKnowledgeDocumentORM,
                ChatKnowledgeChunkORM.document_id == ChatKnowledgeDocumentORM.id,
            )
            .where(
                *await self._eligible_documents(locale, now),
                ChatKnowledgeChunkORM.tsv.is_not(None),
                ChatKnowledgeChunkORM.tsv.op("@@")(tsq),
            )
            .order_by(rank.desc())
            .limit(_FTS_CANDIDATES)
        )
        result = await session.execute(stmt)
        hits: list[RetrievedKnowledgeChunk] = []
        for chunk, document, fts_rank in result.all():
            hits.append(_to_chunk(chunk, document, fts_rank=float(fts_rank or 0.0)))
        return hits

    async def _vector_search(
        self,
        session: AsyncSession,
        query_embedding: list[float],
        locale: str,
        now: datetime,
    ) -> list[RetrievedKnowledgeChunk]:
        distance = ChatKnowledgeChunkORM.embedding.cosine_distance(query_embedding)
        stmt = (
            select(
                ChatKnowledgeChunkORM,
                ChatKnowledgeDocumentORM,
                distance.label("distance"),
            )
            .join(
                ChatKnowledgeDocumentORM,
                ChatKnowledgeChunkORM.document_id == ChatKnowledgeDocumentORM.id,
            )
            .where(
                *await self._eligible_documents(locale, now),
                ChatKnowledgeChunkORM.embedding.is_not(None),
            )
            .order_by(distance.asc())
            .limit(_VECTOR_CANDIDATES)
        )
        result = await session.execute(stmt)
        hits: list[RetrievedKnowledgeChunk] = []
        for chunk, document, distance_value in result.all():
            score = 1.0 - float(distance_value or 1.0)
            hits.append(_to_chunk(chunk, document, vector_score=score))
        return hits


def _passes_threshold(chunk: RetrievedKnowledgeChunk) -> bool:
    if chunk.vector_score is not None and chunk.vector_score >= _MIN_VECTOR_SCORE:
        return True
    if chunk.fts_rank is not None and chunk.fts_rank >= _MIN_FTS_RANK:
        return True
    return False


def _to_chunk(
    chunk: ChatKnowledgeChunkORM,
    document: ChatKnowledgeDocumentORM,
    *,
    vector_score: float | None = None,
    fts_rank: float | None = None,
) -> RetrievedKnowledgeChunk:
    return RetrievedKnowledgeChunk(
        chunk_id=chunk.id,
        document_id=document.id,
        source_key=document.source_key,
        title=document.title,
        content=chunk.content,
        locale=document.locale,
        canonical_uri=document.canonical_uri,
        label="",
        vector_score=vector_score,
        fts_rank=fts_rank,
        safety_tags=tuple(document.safety_tags or ()),
    )
