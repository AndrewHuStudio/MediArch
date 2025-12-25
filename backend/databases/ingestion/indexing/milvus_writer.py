"""
Milvus 写入器（优化版 v2 - 2025-01-22）

核心改进：
- 增加关键过滤字段：page_number、section、year、content_type
- 余弦向量预归一化（入库与查询）
- 批量插入更快路径（np.float32 批处理）
- nlist/nprobe 成对配置 + 索引存在性检查
- 避免每次 search() 反复 load()
- 更健壮的维度与内容裁剪
"""

from typing import List, Dict, Optional, Tuple
from pymilvus import connections, Collection, FieldSchema, CollectionSchema, DataType, utility
import numpy as np
import math


class MilvusWriter:
    """Milvus向量写入器（增强版）"""

    COLLECTION_NAME = "mediarch_chunks"
    VECTOR_DIM = 3072

    def __init__(
        self,
        host: str = "localhost",
        port: str = "19530",
        metric_type: str = "COSINE",
        index_type: str = "IVF_FLAT",
        nlist: int = 1024,
        auto_load: bool = True,
    ):
        connections.connect("default", host=host, port=port)
        self.metric_type = metric_type.upper()
        self.index_type = index_type.upper()
        self.nlist = int(nlist)
        self._loaded = False
        self._ensure_collection_and_index()
        if auto_load:
            self.collection.load()
            self._loaded = True
    
    def _ensure_collection_and_index(self):
        """确保 collection 与 index 存在且匹配"""
        def create_collection():
            fields = [
                FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
                FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=self.VECTOR_DIM),

                # 基础字段
                FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=100),
                FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, max_length=100),
                FieldSchema(name="doc_type", dtype=DataType.VARCHAR, max_length=50),
                FieldSchema(name="source_document", dtype=DataType.VARCHAR, max_length=300),  # 增大到300
                FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=8000),  # 增大到8000

                # ✨ 新增过滤字段
                FieldSchema(name="page_number", dtype=DataType.INT32),      # 页码过滤
                FieldSchema(name="section", dtype=DataType.VARCHAR, max_length=200),  # 章节过滤
                FieldSchema(name="year", dtype=DataType.INT32),             # 年份过滤
                FieldSchema(name="content_type", dtype=DataType.VARCHAR, max_length=20),  # 内容类型
            ]
            schema = CollectionSchema(fields, description="MediArch文档向量库（增强版）")
            return Collection(self.COLLECTION_NAME, schema)

        if utility.has_collection(self.COLLECTION_NAME):
            self.collection = Collection(self.COLLECTION_NAME)

            # 检查是否有新字段（版本兼容）
            field_names = {f.name for f in self.collection.schema.fields}
            required_fields = {"page_number", "section", "year", "content_type"}

            if not required_fields.issubset(field_names):
                print(f"[Milvus] 检测到字段缺失，重建 collection: {required_fields - field_names}")
                self.collection.drop()
                self.collection = create_collection()

        else:
            self.collection = create_collection()

        # 索引检查/创建
        need_create = True
        for idx in (self.collection.indexes or []):
            if getattr(idx, "field_name", "") == "vector":
                params = idx.params or {}
                if params.get("metric_type", "").upper() == self.metric_type and params.get("index_type", "").upper() == self.index_type:
                    need_create = False
                else:
                    self.collection.drop_index("vector")
                break
        if need_create:
            index_params = {
                "metric_type": self.metric_type,
                "index_type": self.index_type,
                "params": {"nlist": self.nlist} if "IVF" in self.index_type else {},
            }
            self.collection.create_index("vector", index_params)
    
    def insert_vectors(
        self,
        chunks: List[Dict],
        doc_id: str,
        batch_size: int = 500,
        normalize: bool = True,
        truncate_content_to: int = 7800,  # 留buffer避免超限
    ) -> Tuple[int, int]:
        """
        插入向量（增强版，支持新字段）

        Args:
            chunks: chunk列表（包含embedding）
            doc_id: MongoDB文档ID
            batch_size: 批量大小
            normalize: 是否归一化向量
            truncate_content_to: content截断长度

        Returns:
            (成功插入数, 跳过数)
        """
        if not chunks:
            return (0, 0)

        ok_total, skip_total = 0, 0

        def prep_batch(part: List[Dict]):
            vecs, doc_ids, chunk_ids, doc_types, source_docs, contents = [], [], [], [], [], []
            page_numbers, sections, years, content_types = [], [], [], []
            skipped = 0

            for c in part:
                emb = c.get("embedding")
                if emb is None:
                    skipped += 1
                    continue
                arr = np.asarray(emb, dtype=np.float32).reshape(-1)
                if arr.size == 0:
                    skipped += 1
                    continue
                if arr.size > self.VECTOR_DIM:
                    arr = arr[: self.VECTOR_DIM]
                elif arr.size < self.VECTOR_DIM:
                    pad = np.zeros(self.VECTOR_DIM - arr.size, dtype=np.float32)
                    arr = np.concatenate([arr, pad], axis=0)

                vecs.append(arr)
                doc_ids.append(doc_id)
                chunk_ids.append(c.get("chunk_id", ""))
                doc_types.append(c.get("doc_type", ""))
                source_docs.append(
                    (c.get("source_document") or c.get("doc_title") or "")[:300]  # 截断到300
                )
                contents.append((c.get("content") or "")[:truncate_content_to])

                # ✨ 新字段提取
                page_numbers.append(c.get("metadata", {}).get("page_number") or c.get("page_range", [1])[0])
                sections.append((c.get("section") or c.get("metadata", {}).get("section") or "")[:200])
                years.append(c.get("metadata", {}).get("year") or 0)
                content_types.append((c.get("content_type") or "text")[:20])

            if not vecs:
                return None, skipped

            V = np.vstack(vecs)
            if normalize and self.metric_type == "COSINE":
                V = V / np.maximum(np.linalg.norm(V, axis=1, keepdims=True), 1e-12)

            return [
                V.tolist(),
                doc_ids,
                chunk_ids,
                doc_types,
                source_docs,
                contents,
                page_numbers,
                sections,
                years,
                content_types
            ], skipped

        for i in range(0, len(chunks), batch_size):
            part = chunks[i : i + batch_size]
            data_cols, skipped = prep_batch(part)
            skip_total += skipped
            if data_cols is None:
                continue
            self.collection.insert(data_cols)
            ok_total += len(data_cols[1])

        self.collection.flush()
        self._ensure_loaded()
        return (ok_total, skip_total)
    
    def search(
        self,
        query_vector: List[float],
        top_k: int = 10,
        nprobe: Optional[int] = None,
        normalize: bool = True,
        output_fields: Optional[List[str]] = None,
    ) -> List[Dict]:
        """
        向量检索
        
        Args:
            query_vector: 查询向量
            top_k: 返回top-k结果
        
        Returns:
            检索结果列表
        """
        self._ensure_loaded()
        if output_fields is None:
            output_fields = ["doc_id", "chunk_id", "doc_type", "source_document", "content",
                           "page_number", "section", "year", "content_type"]  # 包含新字段

        q = np.asarray(query_vector, dtype=np.float32).reshape(-1)
        if q.size != self.VECTOR_DIM:
            if q.size > self.VECTOR_DIM:
                q = q[: self.VECTOR_DIM]
            else:
                pad = np.zeros(self.VECTOR_DIM - q.size, dtype=np.float32)
                q = np.concatenate([q, pad], axis=0)
        if normalize and self.metric_type == "COSINE":
            q = q / np.maximum(np.linalg.norm(q), 1e-12)

        if nprobe is None and "IVF" in self.index_type:
            nprobe = int(max(8, min(128, round(self.nlist * 0.05))))
        search_params = {"metric_type": self.metric_type, "params": ({"nprobe": int(nprobe)} if nprobe is not None else {})}
        results = self.collection.search(
            data=[q.tolist()],
            anns_field="vector",
            param=search_params,
            limit=int(top_k),
            output_fields=output_fields,
        )
        out = []
        for hit in results[0]:
            ent = hit.entity
            item = {f: ent.get(f) for f in output_fields}
            item["distance"] = float(hit.distance)
            out.append(item)
        return out

    def _ensure_loaded(self):
        if not self._loaded:
            self.collection.load()
            self._loaded = True

    def delete_by_doc_id(self, doc_id: str) -> int:
        """
        删除指定 doc_id 的向量记录（用于 FORCE_REINGEST 场景避免重复向量）。

        Returns:
            预计删除的条数（Milvus delete 为异步标记，最终以 compaction 为准）。
        """
        doc_id = (doc_id or "").strip()
        if not doc_id:
            return 0

        # Milvus expr 需要双引号包裹字符串
        safe_doc_id = doc_id.replace("\\", "\\\\").replace('"', '\\"')
        expr = f'doc_id == "{safe_doc_id}"'

        # delete 不一定要求 load，但某些版本下 load 后更稳定
        try:
            self._ensure_loaded()
        except Exception:
            pass

        res = self.collection.delete(expr)
        try:
            self.collection.flush()
        except Exception:
            pass

        deleted = 0
        try:
            deleted = int(getattr(res, "delete_count", 0) or 0)
        except Exception:
            deleted = 0
        return deleted

    def delete_by_chunk_ids(self, chunk_ids: List[str], batch_size: int = 128) -> int:
        """
        删除指定 chunk_id 的向量记录（用于局部重建/回填，避免重复向量）。

        Notes:
            - 当前 collection 的主键为 auto_id，因此需要显式 delete 再 insert 才能“更新”向量。
            - Milvus delete 为异步标记；flush 后检索通常不会返回已删除实体，但最终回收依赖 compaction。

        Args:
            chunk_ids: chunk_id 列表
            batch_size: expr 中 in 列表的分批大小（避免 expr 过长）

        Returns:
            预计删除条数（以 delete_count 为准；不同版本可能为 0）。
        """
        ids = [str(x).strip() for x in (chunk_ids or []) if str(x).strip()]
        if not ids:
            return 0

        try:
            self._ensure_loaded()
        except Exception:
            pass

        deleted_total = 0
        bs = max(1, int(batch_size))
        for i in range(0, len(ids), bs):
            part = ids[i : i + bs]
            safe = [s.replace("\\", "\\\\").replace('"', '\\"') for s in part]
            expr = "chunk_id in [" + ",".join([f'"{s}"' for s in safe]) + "]"
            res = self.collection.delete(expr)
            try:
                deleted_total += int(getattr(res, "delete_count", 0) or 0)
            except Exception:
                pass

        try:
            self.collection.flush()
        except Exception:
            pass

        return int(deleted_total)
