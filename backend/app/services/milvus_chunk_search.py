"""
Milvus 文档 Chunk 向量检索工具

数据源：
- Collection: mediarch_chunks
- Vector: 与入库 embedding 维度一致（通常为 text-embedding-3-large 的 3072 维）

说明：
- 本模块用于“检索文档 chunks（包含图片 caption）”，以便后续通过 MongoDB 以 chunk_id 精确取回
  含 image_url / content_type 的完整信息，从而在前端实现图文并茂的检索效果。
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, List, Optional

from backend.env_loader import load_dotenv
from pymilvus import Collection, connections, utility

from backend.databases.ingestion.indexing.embedding import EmbeddingGenerator

load_dotenv()
logger = logging.getLogger("milvus_chunk_search")


class MilvusChunkRetriever:
    """Milvus 文档 chunks 检索器（mediarch_chunks）"""

    def __init__(self):
        self.host = os.getenv("MILVUS_HOST", "localhost")
        self.port = os.getenv("MILVUS_PORT", "19530")
        self.collection_name = os.getenv("MILVUS_CHUNK_COLLECTION", "mediarch_chunks")

        try:
            connections.connect(alias="default", host=self.host, port=self.port)
        except Exception as e:
            raise RuntimeError(f"Milvus 连接失败: {e}") from e

        if not utility.has_collection(self.collection_name):
            raise RuntimeError(f"Milvus collection 不存在: {self.collection_name}")

        self.collection = Collection(self.collection_name)
        self.collection.load()

        # 复用与入库一致的 Embedding 配置（EMBEDDING_*）
        self.embedding = EmbeddingGenerator()

        self.vector_dim = self._get_vector_dim()

    def _get_vector_dim(self) -> Optional[int]:
        try:
            for field in self.collection.schema.fields:
                if getattr(field, "name", "") == "vector":
                    params = getattr(field, "params", {}) or {}
                    dim = params.get("dim")
                    if dim is not None:
                        return int(dim)
                    # 某些版本 FieldSchema 直接提供 dim 属性
                    if hasattr(field, "dim"):
                        return int(getattr(field, "dim"))
        except Exception:
            return None
        return None

    @staticmethod
    def _normalize(vec: List[float]) -> List[float]:
        norm = math.sqrt(sum((float(v) * float(v)) for v in vec))
        if norm <= 1e-12:
            return vec
        return [float(v) / norm for v in vec]

    def search_chunks(
        self,
        query: str,
        k: int = 8,
        content_type: Optional[str] = None,
        source_documents: Optional[List[str]] = None,
        doc_ids: Optional[List[str]] = None,
        min_similarity: float = 0.0,
        nprobe: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """向量检索文档 chunks（返回 chunk_id 等字段）"""
        if not query or not query.strip():
            return []

        query_vec = self.embedding.generate(query.strip())

        # 维度兜底：与 collection schema 对齐（避免误配导致搜索报错）
        if self.vector_dim and len(query_vec) != self.vector_dim:
            if len(query_vec) > self.vector_dim:
                query_vec = query_vec[: self.vector_dim]
            else:
                query_vec = list(query_vec) + [0.0] * (self.vector_dim - len(query_vec))

        # COSINE 场景下，查询向量也做归一化（入库阶段已做预归一化）
        query_vec = self._normalize(list(query_vec))

        def _escape_expr_str(value: str) -> str:
            return (value or "").replace("\\", "\\\\").replace('"', '\\"')

        expr_parts: List[str] = []
        if content_type:
            expr_parts.append(f'content_type == "{_escape_expr_str(str(content_type))}"')

        if source_documents:
            cleaned = []
            for s in source_documents:
                s = str(s or "").strip()
                if not s:
                    continue
                cleaned.append(s)
            if cleaned:
                uniq = list(dict.fromkeys(cleaned).keys())
                quoted = ", ".join(f'"{_escape_expr_str(v)}"' for v in uniq[:20])
                expr_parts.append(f"source_document in [{quoted}]")

        if doc_ids:
            cleaned = []
            for doc_id in doc_ids:
                doc_id = str(doc_id or "").strip()
                if not doc_id:
                    continue
                cleaned.append(doc_id)
            if cleaned:
                uniq = list(dict.fromkeys(cleaned).keys())
                quoted = ", ".join(f'"{_escape_expr_str(v)}"' for v in uniq[:50])
                expr_parts.append(f"doc_id in [{quoted}]")

        expr = " and ".join(expr_parts) if expr_parts else None

        # IVF 近似检索 + expr 过滤时，nprobe 过低会导致“命中数量不足”（经常 < limit）
        # 这里做一个基于 k 与是否有过滤条件的动态默认值，避免 doc_id/content_type 过滤下召回过低。
        if nprobe is None:
            has_filters = bool(expr_parts)
            if has_filters:
                base = int(os.getenv("MILVUS_CHUNK_SEARCH_NPROBE_FILTERED", "30"))
                cap = int(os.getenv("MILVUS_CHUNK_SEARCH_NPROBE_FILTERED_CAP", "64"))
            else:
                base = int(os.getenv("MILVUS_CHUNK_SEARCH_NPROBE", "10"))
                cap = int(os.getenv("MILVUS_CHUNK_SEARCH_NPROBE_CAP", "32"))
            cap = max(1, cap)
            base = max(1, min(base, cap))
            # k 越大越需要更高 nprobe，避免过滤后“缺结果”
            nprobe = max(base, min(cap, max(1, int(k)) * 2))

        search_params = {"metric_type": "COSINE", "params": {"nprobe": int(nprobe)}}
        output_fields = [
            "doc_id",
            "chunk_id",
            "doc_type",
            "source_document",
            "content",
            "page_number",
            "section",
            "year",
            "content_type",
        ]

        try:
            results = self.collection.search(
                data=[query_vec],
                anns_field="vector",
                param=search_params,
                limit=max(int(k) * 2, 10),
                expr=expr,
                output_fields=output_fields,
            )
        except Exception as e:
            logger.error("[MilvusChunkSearch] Milvus 搜索失败: %s", e)
            return []

        formatted: List[Dict[str, Any]] = []
        if not results:
            return formatted

        for hit in results[0]:
            try:
                score = float(getattr(hit, "score", 0.0))
                if score < float(min_similarity or 0.0):
                    continue

                entity = getattr(hit, "entity", None)
                getter = entity.get if entity else (lambda _k, _d=None: _d)

                formatted.append(
                    {
                        "doc_id": getter("doc_id", ""),
                        "chunk_id": getter("chunk_id", ""),
                        "doc_type": getter("doc_type", ""),
                        "source_document": getter("source_document", ""),
                        "content": getter("content", ""),
                        "page_number": getter("page_number", None),
                        "section": getter("section", ""),
                        "year": getter("year", 0),
                        "content_type": getter("content_type", "text"),
                        "similarity": round(score, 4),
                    }
                )

                if len(formatted) >= int(k):
                    break
            except Exception:
                continue

        return formatted


_retriever: Optional[MilvusChunkRetriever] = None


def get_retriever() -> MilvusChunkRetriever:
    """获取全局检索器实例（单例模式）"""
    global _retriever
    if _retriever is None:
        _retriever = MilvusChunkRetriever()
    return _retriever
