"""
MongoDB 写入器（优化版 v2 - 2025-01-22）

核心改进：
- chunks 独立 collection: mediarch_chunks （避免 16MB 限制）
- ✨ 全文索引：content 字段支持文本搜索
- ✨ 图文关联：parent_chunk_id 关联图片与段落
- 批量写入 bulk_write，ordered=False
- UTC 时间、可靠连接参数、索引保障
- insert_document: 先写 documents，后批量写 chunks（带 doc_id 外键）
- get_document: 可选 include_chunks / limit / projection
"""

from typing import Dict, List, Optional
from datetime import datetime, timezone
from pymongo import MongoClient, ASCENDING, InsertOne, WriteConcern
from pymongo.errors import BulkWriteError
from bson import ObjectId
import logging

log = logging.getLogger("MongoDBWriter")


class MongoDBWriter:
    """MongoDB 文档写入器（documents + mediarch_chunks 分集合）"""

    def __init__(
        self,
        mongo_uri: str,
        database: str = "mediarch",
        doc_coll: str = "documents",
        chunk_coll: str = "mediarch_chunks",
        create_indexes: bool = True,
        majority_write: bool = False,
        server_selection_timeout_ms: int = 5000,
    ):
        self.client = MongoClient(
            mongo_uri,
            retryWrites=True,
            serverSelectionTimeoutMS=server_selection_timeout_ms,
        )
        self.db = self.client[database]

        if majority_write:
            self.documents = self.db.get_collection(doc_coll, write_concern=WriteConcern(w="majority"))
            self.chunks = self.db.get_collection(chunk_coll, write_concern=WriteConcern(w="majority"))
        else:
            self.documents = self.db[doc_coll]
            self.chunks = self.db[chunk_coll]

        if create_indexes:
            self._ensure_indexes()

    def _ensure_indexes(self):
        # documents：时间、类型、外部唯一标识（如果有）
        self.documents.create_index([("upload_time", ASCENDING)])
        self.documents.create_index([("doc_type", ASCENDING)])
        self.documents.create_index([("document_id", ASCENDING)], name="document_id_unique", unique=True, sparse=True)

        # chunks：强制 chunk_id 唯一；doc_id+sequence 查询优化；image_url 稀疏
        self.chunks.create_index([("doc_id", ASCENDING), ("sequence", ASCENDING)], name="doc_seq_idx")
        self.chunks.create_index([("chunk_id", ASCENDING)], name="chunk_id_unique", unique=True)
        self.chunks.create_index([("content_type", ASCENDING)])
        self.chunks.create_index([("image_url", ASCENDING)], name="image_url_sparse", sparse=True)

        # ✨ 全文索引（支持中文分词）
        try:
            # 检查是否已存在文本索引
            existing_indexes = list(self.chunks.list_indexes())
            has_text_index = any("text" in idx.get("weights", {}) or idx.get("name") == "content_text_idx"
                                for idx in existing_indexes)

            if not has_text_index:
                self.chunks.create_index(
                    [("content", "text")],
                    name="content_text_idx",
                    default_language="none",  # 使用通用分词（支持中文）
                    weights={"content": 10}   # 权重
                )
                log.info("创建全文索引成功: content_text_idx")
        except Exception as e:
            log.warning(f"创建全文索引失败（可能已存在）: {e}")

        # ✨ 图文关联索引
        try:
            self.chunks.create_index([("parent_chunk_id", ASCENDING)], name="parent_chunk_idx", sparse=True)
        except Exception:
            pass

        # ✨ 页码与章节索引（加速检索）
        try:
            self.chunks.create_index([("page_range", ASCENDING)])
            self.chunks.create_index([("section", ASCENDING)])
        except Exception:
            pass
    
    def insert_document(
        self, 
        doc_metadata: Dict, 
        chunks: List[Dict],
        strip_large_fields: bool = True,
        chunk_batch_size: int = 1000,
        content_max_chars: int = 10000,
    ) -> str:
        """
        插入文档（documents）+ 批量插入分块（mediarch_chunks）
        返回文档 ObjectId 的字符串
        """
        now = datetime.now(timezone.utc)

        text_cnt = sum(1 for c in chunks if c.get("content_type") == "text")
        table_cnt = sum(1 for c in chunks if c.get("content_type") == "table")
        image_cnt = sum(1 for c in chunks if c.get("content_type") == "image")

        document = {
            **doc_metadata,
            "upload_time": now,
            "statistics": {
                "total_chunks": len(chunks),
                "text_chunks": text_cnt,
                "table_chunks": table_cnt,
                "image_chunks": image_cnt,
            },
        }
        res = self.documents.insert_one(document)
        doc_id = res.inserted_id

        to_insert, inserted, skipped = [], 0, 0
        for c in chunks:
            item = dict(c)
            item["doc_id"] = doc_id
            if strip_large_fields:
                item.pop("embedding", None)
                item.pop("image_bytes", None)
            if isinstance(item.get("content"), str) and len(item["content"]) > content_max_chars:
                item["content"] = item["content"][:content_max_chars]
            to_insert.append(InsertOne(item))
            if len(to_insert) >= chunk_batch_size:
                ins, sk = self._bulk_insert(to_insert)
                inserted += ins; skipped += sk
                to_insert.clear()
        if to_insert:
            ins, sk = self._bulk_insert(to_insert)
            inserted += ins; skipped += sk

        log.info(
            "MongoDBWriter.insert_document: doc_id=%s total=%d inserted=%d skipped=%d",
            str(doc_id), len(chunks), inserted, skipped,
        )
        return str(doc_id)

    def _bulk_insert(self, ops: List[InsertOne], doc_id: Optional[str] = None):
        if not ops:
            return 0, 0
        try:
            res = self.chunks.bulk_write(ops, ordered=False)
            return (res.inserted_count or 0), 0
        except BulkWriteError as e:
            details = e.details or {}
            write_errors = details.get("writeErrors", [])
            log.warning(
                "bulk write partial errors: %d/%d (e.g., duplicate chunk_id) doc_id=%s ops_size=%d",
                len(write_errors), len(ops), str(doc_id) if doc_id else "-", len(ops)
            )
            inserted = (details.get("nInserted") or 0)
            skipped = len(write_errors)
            return inserted, skipped

    def get_document(
        self,
        doc_id: str,
        include_chunks: bool = True,
        chunk_limit: Optional[int] = None,
        chunk_skip: int = 0,
        chunk_projection: Optional[Dict] = None,
    ) -> Dict:
        oid = ObjectId(doc_id)
        doc = self.documents.find_one({"_id": oid})
        if not doc or not include_chunks:
            return doc or {}
        if chunk_projection is None:
            chunk_projection = {"_id": 0}
        cursor = (
            self.chunks.find({"doc_id": oid}, projection=chunk_projection)
            .sort("sequence", ASCENDING)
            .skip(int(chunk_skip))
        )
        if chunk_limit:
            cursor = cursor.limit(int(chunk_limit))
        doc["chunks"] = list(cursor)
        return doc
    
    def close(self):
        self.client.close()
