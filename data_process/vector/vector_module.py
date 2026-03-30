"""
模块2: 向量化处理

封装现有 ChunkStrategy + EmbeddingGenerator + MilvusWriter + MongoDBWriter，
新增 BgeReranker 用于检索重排序。
"""

import os
import time
import logging
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pymongo.errors import DuplicateKeyError

from backend.databases.ingestion.indexing.chunking import ChunkStrategy
from backend.databases.ingestion.indexing.embedding import EmbeddingGenerator
from backend.databases.ingestion.indexing.mongodb_writer import MongoDBWriter
from backend.databases.ingestion.indexing.milvus_writer import MilvusWriter
from data_process.vector.reranker import BgeReranker

logger = logging.getLogger(__name__)


def _is_duplicate_document_error(exc: Exception) -> bool:
    if isinstance(exc, DuplicateKeyError):
        return True
    msg = str(exc or "")
    return "E11000 duplicate key error" in msg and "document_id_unique" in msg


@dataclass
class VectorResult:
    """向量化处理结果"""
    doc_id: str
    total_chunks: int
    text_chunks: int
    image_chunks: int
    table_chunks: int
    embeddings_written: int
    chunks_inserted: int
    duration_s: float


class VectorModule:
    """向量化模块 -- Chunking + Embedding + Reranking + DB Write"""

    def __init__(self):
        self.chunk_strategy = ChunkStrategy(
            max_chunk_size=int(os.getenv("CHUNK_MAX", "1200")),
            min_chunk_size=int(os.getenv("CHUNK_MIN", "100")),
            merge_small_chunks=True,
            chunk_overlap=int(os.getenv("CHUNK_OVERLAP", "100")),
            normalize_positions=True,
        )
        self.embedding_gen = EmbeddingGenerator()
        self.mongodb_writer = MongoDBWriter(
            mongo_uri=os.getenv("MONGODB_URI"),
            database=os.getenv("MONGODB_DATABASE", "mediarch"),
        )
        self.milvus_writer = MilvusWriter(
            host=os.getenv("MILVUS_HOST", "localhost"),
            port=os.getenv("MILVUS_PORT", "19530"),
        )
        self.reranker: Optional[BgeReranker] = None
        self.embed_batch_size = max(
            1, int(os.getenv("VECTOR_EMBED_BATCH_SIZE", "100"))
        )
        self.mongo_chunk_batch_size = max(
            1, int(os.getenv("VECTOR_MONGO_CHUNK_BATCH_SIZE", "1000"))
        )

    def vectorize_document(
        self,
        ocr_result: Dict[str, Any],
        doc_metadata: Dict[str, Any],
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> VectorResult:
        """完整向量化流程。

        流程:
            1. ChunkStrategy.chunk_by_hierarchy() 分块
            2. EmbeddingGenerator.generate_batch() 生成向量
            3. MongoDBWriter 写入文档元数据
            4. MilvusWriter + MongoDBWriter 写入 chunks

        Args:
            ocr_result: TextIn 兼容格式的 OCR 结果 (来自 OcrModule 或 MineruClient)
            doc_metadata: 文档元数据 dict，需包含 title, category, file_path 等
            progress_callback: fn(step_name, current, total)
        """
        t0 = time.time()

        # Step 1: 分块
        if progress_callback:
            progress_callback("chunking", 0, 4)
        logger.info("Vectorize: chunking document '%s'", doc_metadata.get("title", ""))

        chunks = self.chunk_strategy.chunk_by_hierarchy(
            textin_result=ocr_result,
            doc_metadata=doc_metadata,
        )

        text_chunks = [c for c in chunks if c.get("content_type") == "text" and (c.get("content") or "").strip()]
        image_chunks = [c for c in chunks if c.get("content_type") == "image"]
        table_chunks = [c for c in chunks if c.get("content_type") == "table"]
        all_with_content = [c for c in chunks if (c.get("content") or "").strip()]

        logger.info("Vectorize: %d chunks (text=%d, image=%d, table=%d)",
                     len(chunks), len(text_chunks), len(image_chunks), len(table_chunks))

        # Step 2: 生成向量（分批，在 Step4 中边算边写，降低峰值内存）
        if progress_callback:
            progress_callback("embedding", 1, 4)
        logger.info(
            "Vectorize: preparing streaming embedding for %d chunks (batch_size=%d)",
            len(all_with_content),
            self.embed_batch_size,
        )

        # Step 3: 写入 MongoDB 文档记录
        if progress_callback:
            progress_callback("writing_mongo", 2, 4)

        full_doc_metadata = {
            "document_id": doc_metadata.get("file_path", ""),
            "title": doc_metadata.get("title", ""),
            "category": doc_metadata.get("category", ""),
            "type": doc_metadata.get("category", ""),
            "source_document": doc_metadata.get("title", ""),
            "upload_time": datetime.now(timezone.utc),
        }

        doc_identifier = str(full_doc_metadata.get("document_id") or "").strip()
        if not doc_identifier:
            raise RuntimeError("doc_metadata.file_path is required for document_id")

        try:
            mongo_doc_id = str(
                self.mongodb_writer.documents.insert_one(full_doc_metadata).inserted_id
            )
        except Exception as exc:
            if not _is_duplicate_document_error(exc):
                raise

            existing = self.mongodb_writer.documents.find_one(
                {"document_id": doc_identifier},
                {"_id": 1, "statistics": 1},
            )
            if not existing or existing.get("_id") is None:
                raise

            stats = existing.get("statistics") or {}
            duration = time.time() - t0
            if progress_callback:
                progress_callback("done", 4, 4)
            logger.info(
                "Vectorize idempotent hit: document_id=%s, existing_doc_id=%s",
                doc_identifier,
                str(existing.get("_id")),
            )
            return VectorResult(
                doc_id=str(existing.get("_id")),
                total_chunks=int(stats.get("total_chunks") or 0),
                text_chunks=int(stats.get("text_chunks") or 0),
                image_chunks=int(stats.get("image_chunks") or 0),
                table_chunks=int(stats.get("table_chunks") or 0),
                embeddings_written=0,
                chunks_inserted=0,
                duration_s=duration,
            )

        # Step 4: 写入 Milvus 向量 + MongoDB chunks
        if progress_callback:
            progress_callback("writing_vectors", 3, 4)

        embeddings_written = 0
        milvus_errors: List[str] = []
        if all_with_content:
            for start in range(0, len(all_with_content), self.embed_batch_size):
                batch_chunks = all_with_content[start : start + self.embed_batch_size]
                batch_texts = [c.get("content", "") for c in batch_chunks]

                # embedding.py 内部仍有重试/缓存，这里只控制外层批次粒度。
                batch_embs = self.embedding_gen.generate_batch(
                    batch_texts,
                    batch_size=min(100, max(1, len(batch_texts))),
                )

                filled_batch: List[Dict[str, Any]] = []
                for chunk, emb in zip(batch_chunks, batch_embs):
                    if emb is None:
                        continue
                    chunk["embedding"] = emb
                    filled_batch.append(chunk)

                if not filled_batch:
                    continue

                try:
                    self.milvus_writer.insert_vectors(
                        chunks=filled_batch, doc_id=mongo_doc_id
                    )
                    embeddings_written += len(filled_batch)
                except Exception as e:
                    logger.error(
                        "Milvus write failed on batch [%d:%d]: %s",
                        start,
                        start + len(filled_batch),
                        e,
                    )
                    milvus_errors.append(
                        f"batch[{start}:{start + len(filled_batch)}]={e}"
                    )
                finally:
                    # 无论写入成功与否，都尽快释放 embedding 占用。
                    for item in filled_batch:
                        item.pop("embedding", None)

        chunks_inserted = 0
        mongo_errors: List[str] = []
        if chunks:
            for start in range(0, len(chunks), self.mongo_chunk_batch_size):
                batch = chunks[start : start + self.mongo_chunk_batch_size]
                chunk_ops = []
                for c in batch:
                    item = dict(c)
                    item["doc_id"] = mongo_doc_id
                    item.pop("embedding", None)
                    chunk_ops.append(item)
                if not chunk_ops:
                    continue
                try:
                    result = self.mongodb_writer.chunks.insert_many(
                        chunk_ops, ordered=False
                    )
                    chunks_inserted += len(result.inserted_ids)
                except Exception as e:
                    logger.error(
                        "MongoDB chunks write failed on batch [%d:%d]: %s",
                        start,
                        start + len(chunk_ops),
                        e,
                    )
                    mongo_errors.append(
                        f"batch[{start}:{start + len(chunk_ops)}]={e}"
                    )

        if milvus_errors or mongo_errors:
            problems = []
            if milvus_errors:
                problems.append(f"milvus_write_errors={'; '.join(milvus_errors[:3])}")
            if mongo_errors:
                problems.append(f"mongo_write_errors={'; '.join(mongo_errors[:3])}")
            raise RuntimeError("Vector write not fully successful: " + " | ".join(problems))

        duration = time.time() - t0

        if progress_callback:
            progress_callback("done", 4, 4)

        logger.info("Vectorize done: doc_id=%s, chunks=%d, embeddings=%d, %.1fs",
                     mongo_doc_id, chunks_inserted, embeddings_written, duration)

        return VectorResult(
            doc_id=mongo_doc_id,
            total_chunks=len(chunks),
            text_chunks=len(text_chunks),
            image_chunks=len(image_chunks),
            table_chunks=len(table_chunks),
            embeddings_written=embeddings_written,
            chunks_inserted=chunks_inserted,
            duration_s=round(duration, 2),
        )

    def rerank_chunks(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        top_k: int = 10,
    ) -> List[Dict[str, Any]]:
        """使用配置的 reranker 模型对 chunks 重排序。

        Args:
            query: 查询字符串
            chunks: chunk 列表，需包含 "content" 字段
            top_k: 返回 top-k 结果
        """
        if self.reranker is None:
            self.reranker = BgeReranker()
        return self.reranker.rerank(query, chunks, top_k=top_k)
