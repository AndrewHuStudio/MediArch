from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from backend.app.agents.base_agent import AgentItem
from backend.app.services.mongodb_search import MongoDBChunkRetriever
from backend.app.services.milvus_chunk_search import MilvusChunkRetriever
from backend.app.services.query_expansion import QueryExpansion, get_query_expansion_runtime_status


_TERM_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}")
_LATIN_RE = re.compile(r"[A-Za-z0-9]+")
_CHINESE_SPAN_RE = re.compile(r"[\u4e00-\u9fff]+")
_TOKENIZER = QueryExpansion(use_jieba=get_query_expansion_runtime_status()["jieba_available"])


def tokenize_bm25_text(text: str) -> List[str]:
    """Tokenize mixed Chinese/English text for local BM25 scoring."""
    text = (text or "").strip().lower()
    if not text:
        return []

    tokens: List[str] = []
    try:
        tokens.extend(_TOKENIZER.tokenize(text))
    except Exception:
        tokens.extend(_TERM_RE.findall(text))

    for part in _LATIN_RE.findall(text):
        tokens.append(part)

    for span in _CHINESE_SPAN_RE.findall(text):
        span = span.strip()
        if len(span) < 2:
            continue
        max_n = min(4, len(span))
        for n in range(2, max_n + 1):
            for i in range(len(span) - n + 1):
                tokens.append(span[i : i + n])

    cleaned: List[str] = []
    seen: set[str] = set()
    for token in tokens:
        token = str(token or "").strip().lower()
        if not token or len(token) < 2 or token in seen:
            continue
        seen.add(token)
        cleaned.append(token)
    return cleaned


def _chunk_text(chunk: Dict[str, Any]) -> str:
    return str(
        chunk.get("chunk_text")
        or chunk.get("content")
        or chunk.get("snippet")
        or ""
    )


def build_bm25_ranked_chunks(
    query: str,
    chunks: Sequence[Dict[str, Any]],
    limit: int = 8,
) -> List[Dict[str, Any]]:
    """Rank candidate chunks with a small local BM25 implementation."""
    docs = list(chunks or [])
    if not docs:
        return []

    query_tokens = tokenize_bm25_text(query)
    if not query_tokens:
        return list(docs[:limit])

    tokenized_docs = [tokenize_bm25_text(_chunk_text(chunk)) for chunk in docs]
    doc_count = len(tokenized_docs)
    avgdl = sum(len(tokens) for tokens in tokenized_docs) / max(doc_count, 1)
    avgdl = avgdl or 1.0

    df: Counter[str] = Counter()
    for tokens in tokenized_docs:
        for token in set(tokens):
            df[token] += 1

    k1 = float(os.getenv("BM25_K1", "1.5"))
    b = float(os.getenv("BM25_B", "0.75"))

    scored: List[tuple[int, Dict[str, Any]]] = []
    for idx, chunk in enumerate(docs):
        tokens = tokenized_docs[idx]
        if not tokens:
            score = 0.0
        else:
            tf = Counter(tokens)
            doc_len = len(tokens)
            score = 0.0
            for term in query_tokens:
                freq = tf.get(term, 0)
                if not freq:
                    continue
                doc_freq = df.get(term, 0)
                idf = math.log(1.0 + (doc_count - doc_freq + 0.5) / (doc_freq + 0.5))
                denom = freq + k1 * (1.0 - b + b * (doc_len / avgdl))
                score += idf * (freq * (k1 + 1.0)) / denom

        ranked = dict(chunk)
        ranked["bm25_score"] = round(score, 6)
        scored.append((idx, ranked))

    scored.sort(
        key=lambda pair: (
            -float(pair[1].get("bm25_score") or 0.0),
            pair[0],
        ),
    )
    return [row for _, row in scored[: max(int(limit), 1)]]


def _page_number_from_chunk(chunk: Dict[str, Any]) -> Optional[int]:
    page_number = chunk.get("page_number")
    if isinstance(page_number, int):
        return page_number
    if isinstance(page_number, str) and page_number.strip().isdigit():
        return int(page_number.strip())

    page_range = chunk.get("page_range")
    if isinstance(page_range, (list, tuple)) and page_range:
        first = page_range[0]
        if isinstance(first, int):
            return first
        if isinstance(first, str) and first.strip().isdigit():
            return int(first.strip())
    return None


def chunk_to_agent_item(
    chunk: Dict[str, Any],
    *,
    source: str,
    retrieval_score_type: str = "bm25",
) -> AgentItem:
    """Convert a retrieved chunk to a standard AgentItem."""
    source_document = str(chunk.get("source_document") or chunk.get("doc_title") or chunk.get("doc_name") or "未知来源")
    section = str(chunk.get("section") or "").strip()
    page_number = _page_number_from_chunk(chunk)
    chunk_id = str(chunk.get("chunk_id") or "").strip()
    snippet = _chunk_text(chunk)
    if len(snippet) > 240:
        snippet = snippet[:240].rstrip() + "..."

    citation = {
        "source": source_document,
        "chunk_id": chunk_id,
        "location": section or (f"第{page_number}页" if page_number else "位置待查"),
        "page_number": page_number,
        "section": section,
        "content_type": str(chunk.get("content_type") or "text"),
        "snippet": snippet,
        "doc_id": chunk.get("doc_id"),
        "file_path": chunk.get("file_path"),
        "document_path": chunk.get("document_path"),
    }

    return AgentItem(
        entity_id=chunk_id or None,
        name=source_document,
        label="Chunk",
        score=float(chunk.get("bm25_score") or chunk.get("similarity") or 0.0),
        attrs={
            "source_document": source_document,
            "section": section,
            "page_number": page_number,
            "doc_id": chunk.get("doc_id"),
            "content_type": str(chunk.get("content_type") or "text"),
            "retrieval_score_type": retrieval_score_type,
            "retrieval_source": source,
            "bm25_score": chunk.get("bm25_score"),
            "similarity": chunk.get("similarity"),
        },
        citations=[citation],
        source=source,
        snippet=snippet,
    )


@dataclass
class BaselineRAGResult:
    items: List[AgentItem]
    diagnostics: Dict[str, Any]


class BaselineRAGService:
    """Shared baseline retrieval service for BM25 and vector RAG."""

    def __init__(
        self,
        *,
        mongodb_retriever: Optional[MongoDBChunkRetriever] = None,
        milvus_retriever: Optional[MilvusChunkRetriever] = None,
    ) -> None:
        self.mongodb_retriever = mongodb_retriever
        self.milvus_retriever = milvus_retriever

    def _get_mongodb(self) -> MongoDBChunkRetriever:
        if self.mongodb_retriever is None:
            self.mongodb_retriever = MongoDBChunkRetriever()
        return self.mongodb_retriever

    def _get_milvus(self) -> MilvusChunkRetriever:
        if self.milvus_retriever is None:
            self.milvus_retriever = MilvusChunkRetriever()
        return self.milvus_retriever

    def retrieve_bm25(
        self,
        query: str,
        *,
        top_k: int = 8,
        doc_ids: Optional[List[str]] = None,
        source_documents: Optional[List[str]] = None,
    ) -> BaselineRAGResult:
        retriever = self._get_mongodb()
        doc_id_values = retriever._resolve_doc_id_values(doc_ids=doc_ids, source_documents=source_documents)
        query_filter: Dict[str, Any] = {}
        if doc_id_values:
            query_filter["doc_id"] = {"$in": doc_id_values}

        projection = {
            "_id": 0,
            "chunk_id": 1,
            "content": 1,
            "doc_id": 1,
            "doc_title": 1,
            "doc_category": 1,
            "metadata": 1,
            "page_range": 1,
            "section": 1,
            "image_url": 1,
            "content_type": 1,
            "positions": 1,
        }
        candidates = list(retriever.chunks_collection.find(query_filter, projection))
        for chunk in candidates:
            if not chunk.get("source_document"):
                chunk["source_document"] = (
                    chunk.get("doc_title")
                    or chunk.get("doc_category")
                    or "未知来源"
                )

        ranked = build_bm25_ranked_chunks(query, candidates, limit=top_k)
        items = [chunk_to_agent_item(chunk, source="bm25_baseline", retrieval_score_type="bm25") for chunk in ranked]
        return BaselineRAGResult(
            items=items,
            diagnostics={
                "mode": "BM25",
                "candidate_count": len(candidates),
                "returned_count": len(items),
                "tokenizer": get_query_expansion_runtime_status()["tokenizer_backend"],
            },
        )

    def retrieve_vector(
        self,
        query: str,
        *,
        top_k: int = 8,
        doc_ids: Optional[List[str]] = None,
        source_documents: Optional[List[str]] = None,
    ) -> BaselineRAGResult:
        retriever = self._get_milvus()
        chunks = retriever.search_chunks(
            query=query,
            k=top_k,
            source_documents=source_documents,
            doc_ids=doc_ids,
            min_similarity=0.0,
            nprobe=None,
        )
        items = [
            chunk_to_agent_item(chunk, source="vector_rag_baseline", retrieval_score_type="vector")
            for chunk in chunks
        ]
        return BaselineRAGResult(
            items=items,
            diagnostics={
                "mode": "VRAG",
                "candidate_count": len(chunks),
                "returned_count": len(items),
                "embedding_dim": 3072,
                "metric": "COSINE",
                "index": "IVF_FLAT",
            },
        )


_baseline_service: Optional[BaselineRAGService] = None


def get_baseline_rag_service() -> BaselineRAGService:
    global _baseline_service
    if _baseline_service is None:
        _baseline_service = BaselineRAGService()
    return _baseline_service
