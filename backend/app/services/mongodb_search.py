# app/tools/mongodb_search.py
"""
MongoDB 文档块检索工具

功能：
1. 从 MongoDB 检索完整的 chunk 内容
2. 提供 chunk 的上下文信息
3. 补充图谱和向量检索之外的详细信息

数据源：
- Collection: mediarch_chunks
- Database: mediarch
"""

import os
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Union
from dotenv import load_dotenv
from langchain_core.tools import tool
from pymongo import MongoClient
from bson import ObjectId

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DOCUMENTS_DIR = (PROJECT_ROOT / "backend" / "databases" / "documents").resolve()


class MongoDBChunkRetriever:
    """MongoDB 文档块检索器"""
    
    def __init__(self):
        """初始化 MongoDB 连接"""
        self.uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
        self.db_name = "mediarch"
        self.chunks_collection_name = "mediarch_chunks"  # chunks 存储在独立集合
        self.documents_collection_name = "documents"  # 文档元数据
        self._doc_info_cache: Dict[str, Dict[str, Any]] = {}

        self._text_index_enabled = False
        self._text_index_name: Optional[str] = None

        try:
            self.client = MongoClient(self.uri)
            self.db = self.client[self.db_name]
            self.chunks_collection = self.db[self.chunks_collection_name]
            self.documents_collection = self.db[self.documents_collection_name]
            
            # 测试连接
            self.client.server_info()
            self._detect_text_index()
            print(f"[OK] MongoDB连接成功: {self.db_name}.{self.chunks_collection_name}")
        except Exception as e:
            print(f"[ERR] MongoDB连接失败: {e}")
            raise

    def _detect_text_index(self) -> None:
        """检测 content 字段是否已创建文本索引"""
        try:
            for index in self.chunks_collection.list_indexes():
                weights = index.get("weights") or {}
                if "content" in weights or index.get("name") == "content_text_idx":
                    self._text_index_enabled = True
                    self._text_index_name = index.get("name")
                    print(f"[OK] 检测到 MongoDB 文本索引: {self._text_index_name}")
                    break
        except Exception as exc:
            print(f"[WARN] MongoDB 文本索引检测失败: {exc}")
            self._text_index_enabled = False
            self._text_index_name = None

    def _get_doc_info(self, doc_id: Any) -> Dict[str, Any]:
        """获取文档元数据并缓存"""
        if not doc_id:
            return {}

        cache_key = str(doc_id)
        if cache_key in self._doc_info_cache:
            return self._doc_info_cache[cache_key]

        query: Dict[str, Any]
        if isinstance(doc_id, ObjectId):
            query = {"_id": doc_id}
        else:
            try:
                query = {"_id": ObjectId(str(doc_id))}
            except Exception:
                query = {"document_id": str(doc_id)}

        projection = {
            "title": 1,
            "file_path": 1,
            "source_path": 1,
            "document_id": 1,
            "category": 1,
            "doc_type": 1,
        }

        doc = self.documents_collection.find_one(query, projection)

        info: Dict[str, Any] = {}
        if doc:
            info = {
                "title": doc.get("title"),
                "file_path": doc.get("file_path") or doc.get("source_path"),
                "document_id": doc.get("document_id") or str(doc.get("_id")),
                "doc_type": doc.get("doc_type"),
                "category": doc.get("category"),
                "doc_id": str(doc.get("_id")) if doc.get("_id") else str(doc_id),
            }
        else:
            info = {
                "doc_id": str(doc_id),
            }

        self._doc_info_cache[cache_key] = info
        return info

    def _resolve_source_document(self, chunk_doc: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """获取 chunk 对应的文档标题及元数据"""
        doc_id = chunk_doc.get("doc_id")
        doc_info = self._get_doc_info(doc_id)

        doc_title = (
            chunk_doc.get("doc_title")
            or chunk_doc.get("source_document")
            or doc_info.get("title")
            or chunk_doc.get("doc_category")
            or "未知来源"
        )

        return doc_title, doc_info

    def _compute_relative_path(self, file_path: Optional[str]) -> Optional[str]:
        if not file_path:
            return None

        path_obj = Path(file_path)
        if not path_obj.is_absolute():
            abs_path = (DOCUMENTS_DIR / path_obj).resolve()
        else:
            abs_path = path_obj.resolve()

        try:
            return abs_path.relative_to(DOCUMENTS_DIR).as_posix()
        except ValueError:
            return None

    @staticmethod
    def _normalize_source_titles(values: Optional[List[str]]) -> List[str]:
        cleaned: List[str] = []
        for value in values or []:
            s = str(value or "").strip()
            if not s:
                continue
            s = s.replace("\\", "/").split("/")[-1].strip()
            cleaned.append(s)

            # 兼容用户用《》包裹
            if "《" in s or "》" in s:
                cleaned.append(s.replace("《", "").replace("》", "").strip())

            # 兼容无扩展名
            if not s.lower().endswith(".pdf"):
                cleaned.append(f"{s}.pdf")
                cleaned.append(f"{s.replace('《', '').replace('》', '').strip()}.pdf")

        # 去重保持顺序
        uniq: List[str] = []
        seen: set[str] = set()
        for s in cleaned:
            s = str(s or "").strip()
            if not s or s in seen:
                continue
            seen.add(s)
            uniq.append(s)
        return uniq

    @staticmethod
    def _expand_doc_id_values(doc_id: Any) -> List[Any]:
        """将 doc_id 扩展为 str + ObjectId（若可解析）"""
        values: List[Any] = []
        if not doc_id:
            return values
        if isinstance(doc_id, ObjectId):
            values.append(doc_id)
            values.append(str(doc_id))
            return values
        doc_id_str = str(doc_id).strip()
        if not doc_id_str:
            return values
        values.append(doc_id_str)
        try:
            values.append(ObjectId(doc_id_str))
        except Exception:
            pass
        return values

    def _resolve_doc_id_values(
        self,
        doc_ids: Optional[List[Any]] = None,
        source_documents: Optional[List[str]] = None,
    ) -> List[Any]:
        """
        从 doc_ids 或 source_documents 解析可用于 chunks.doc_id 的 $in 列表。

        说明：
        - chunks.doc_id 可能是 ObjectId 或 str，因此同时生成两种形态。
        - 当 doc_ids 为空但给了 source_documents 时，会从 documents.title 反查 _id。
        """
        collected: List[Any] = []

        # 1) doc_ids 直接使用（更精确）
        for doc_id in (doc_ids or []):
            collected.extend(self._expand_doc_id_values(doc_id))

        if collected:
            return list(dict.fromkeys(collected).keys())

        # 2) 用 source_documents 在 documents 集合中反查
        titles = self._normalize_source_titles(source_documents)
        if not titles:
            return []

        cursor = self.documents_collection.find({"title": {"$in": titles}}, {"_id": 1})
        for doc in cursor:
            collected.extend(self._expand_doc_id_values(doc.get("_id")))

        return list(dict.fromkeys(collected).keys())

    def _build_chunk_result(self, chunk_doc: Dict[str, Any]) -> Dict[str, Any]:
        """统一构建 chunk 返回结构"""
        doc_title, doc_info = self._resolve_source_document(chunk_doc)
        file_path = doc_info.get("file_path")
        document_path = self._compute_relative_path(file_path)
        doc_id = chunk_doc.get("doc_id")

        return {
            "chunk_id": chunk_doc.get("chunk_id", ""),
            "chunk_text": chunk_doc.get("content", ""),
            "source_document": doc_title,
            "metadata": chunk_doc.get("metadata", {}),
            "page_range": chunk_doc.get("page_range", []),
            "section": chunk_doc.get("section", ""),
            "image_url": chunk_doc.get("image_url"),
            "content_type": chunk_doc.get("content_type", "text"),
            "doc_title": doc_title,
            "doc_category": chunk_doc.get("doc_category") or doc_info.get("category"),
            "positions": chunk_doc.get("positions", []),
            "file_path": file_path,
            "document_path": document_path,
            "doc_id": str(doc_id) if doc_id else doc_info.get("doc_id"),
        }

    def get_chunks_by_ids(self, chunk_ids: List[str]) -> List[Dict[str, Any]]:
        """
        根据 chunk_id 批量检索文档块
        
        Args:
            chunk_ids: chunk ID 列表
        
        Returns:
            chunk 列表，每个 chunk 包含：
            - chunk_id: 唯一标识
            - chunk_text: 完整文本内容
            - source_document: 来源文档
            - metadata: 元数据（页码、章节等）
        """
        if not chunk_ids:
            return []
        
        try:
            # 从 mediarch_chunks 集合查询
            # [FIX 2025-12-03] 添加 page_range, section, image_url, content_type 字段
            results = self.chunks_collection.find(
                {"chunk_id": {"$in": chunk_ids}},
                {
                    "_id": 0,
                    "chunk_id": 1,
                    "content": 1,
                    "doc_id": 1,
                    "doc_title": 1,
                    "doc_category": 1,
                    "metadata": 1,
                    "page_range": 1,      # [FIX] 页码范围
                    "section": 1,          # [FIX] 章节信息
                    "image_url": 1,        # [FIX] 图片URL
                    "content_type": 1,     # [FIX] 内容类型
                    "positions": 1,
                }
            )
            
            chunks: List[Dict[str, Any]] = []

            for chunk_doc in results:
                chunks.append(self._build_chunk_result(chunk_doc))

            return chunks
            
        except Exception as e:
            print(f"[ERR] MongoDB查询失败: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def search_by_keywords(
        self,
        keywords: str,
        limit: int = 5,
        doc_ids: Optional[List[Any]] = None,
        source_documents: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        根据关键词搜索文档块（文本匹配）

        Args:
            keywords: 关键词
            limit: 返回结果数量

        Returns:
            chunk 列表
        """
        try:
            doc_id_values = self._resolve_doc_id_values(doc_ids=doc_ids, source_documents=source_documents)
            base_filter: Dict[str, Any] = {}
            if doc_id_values:
                base_filter = {"doc_id": {"$in": doc_id_values}}

            # 从 mediarch_chunks 集合搜索
            # [FIX 2025-12-03] 添加 page_range, section, image_url, content_type 字段
            results = self.chunks_collection.find(
                {**base_filter, "content": {"$regex": keywords, "$options": "i"}},
                {
                    "_id": 0,
                    "chunk_id": 1,
                    "content": 1,
                    "doc_id": 1,
                    "doc_title": 1,
                    "doc_category": 1,
                    "metadata": 1,
                    "page_range": 1,      # [FIX] 页码范围
                    "section": 1,          # [FIX] 章节信息
                    "image_url": 1,        # [FIX] 图片URL
                    "content_type": 1,     # [FIX] 内容类型
                    "positions": 1,
                }
            ).limit(limit)
            
            chunks: List[Dict[str, Any]] = []

            for chunk_doc in results:
                chunks.append(self._build_chunk_result(chunk_doc))

            return chunks[:limit]
            
        except Exception as e:
            print(f"[ERR] MongoDB关键词搜索失败: {e}")
            import traceback
            traceback.print_exc()
            return []

    def _search_with_text_index(
        self,
        keywords: List[str],
        limit: int,
        doc_ids: Optional[List[Any]] = None,
        source_documents: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """使用 MongoDB text index 进行搜索"""
        if not keywords:
            return []

        # 使用引号确保多词短语保持一致性
        def _quote(term: str) -> str:
            return f'"{term}"' if " " in term else term

        text_query = " ".join(_quote(term) for term in keywords[:15])

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
            "score": {"$meta": "textScore"},
        }

        doc_id_values = self._resolve_doc_id_values(doc_ids=doc_ids, source_documents=source_documents)
        base_filter: Dict[str, Any] = {}
        if doc_id_values:
            base_filter = {"doc_id": {"$in": doc_id_values}}

        cursor = (
            self.chunks_collection.find(
                {**base_filter, "$text": {"$search": text_query}},
                projection,
            )
            .sort([("score", {"$meta": "textScore"})])
            .limit(limit * 2)
        )

        return [self._build_chunk_result(doc) for doc in cursor]

    def get_image_chunks_near_pages(
        self,
        doc_id: Any,
        near_pages: Optional[List[int]] = None,
        limit: int = 5,
        page_window: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        获取指定文档中“接近某些页码”的图片 chunks（用于“补图”兜底）。

        说明：
        - 先按 near_pages 在 page_range 范围内检索（支持 ±page_window）
        - 不足时回退为该文档内任意图片
        """
        if not doc_id or limit <= 0:
            return []

        # 兼容 doc_id 为 ObjectId / str
        doc_id_values: List[Any] = []
        if isinstance(doc_id, ObjectId):
            doc_id_values.append(doc_id)
        else:
            doc_id_str = str(doc_id).strip()
            if doc_id_str:
                doc_id_values.append(doc_id_str)
                try:
                    doc_id_values.append(ObjectId(doc_id_str))
                except Exception:
                    pass

        doc_id_values = list(dict.fromkeys(doc_id_values).keys())
        if not doc_id_values:
            return []

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

        base_filter: Dict[str, Any] = {
            "doc_id": {"$in": doc_id_values},
            "content_type": "image",
            "image_url": {"$nin": [None, ""]},
        }

        collected: List[Dict[str, Any]] = []
        seen: set[str] = set()

        # Pass 1: near pages
        pages = [p for p in (near_pages or []) if isinstance(p, int)]
        pages = list(dict.fromkeys(pages).keys())[:8]
        if pages:
            for page in pages:
                if len(collected) >= limit:
                    break
                page_filter = {
                    "page_range.0": {"$lte": int(page) + int(page_window)},
                    "page_range.1": {"$gte": int(page) - int(page_window)},
                }
                cursor = (
                    self.chunks_collection.find({**base_filter, **page_filter}, projection)
                    .sort([("page_range.0", 1), ("chunk_id", 1)])
                    .limit(limit * 2)
                )
                for doc in cursor:
                    cid = doc.get("chunk_id") or ""
                    if not cid or cid in seen:
                        continue
                    seen.add(cid)
                    collected.append(self._build_chunk_result(doc))
                    if len(collected) >= limit:
                        break

        # Pass 2: fallback to any image in doc
        if len(collected) < limit:
            cursor = (
                self.chunks_collection.find(base_filter, projection)
                .sort([("page_range.0", 1), ("chunk_id", 1)])
                .limit(limit * 3)
            )
            for doc in cursor:
                cid = doc.get("chunk_id") or ""
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                collected.append(self._build_chunk_result(doc))
                if len(collected) >= limit:
                    break

        return collected[:limit]

    def _search_with_regex(
        self,
        pattern: str,
        limit: int,
        doc_ids: Optional[List[Any]] = None,
        source_documents: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """回退方案：使用正则（OR）搜索"""
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

        doc_id_values = self._resolve_doc_id_values(doc_ids=doc_ids, source_documents=source_documents)
        base_filter: Dict[str, Any] = {}
        if doc_id_values:
            base_filter = {"doc_id": {"$in": doc_id_values}}

        cursor = self.chunks_collection.find(
            {
                **base_filter,
                "content": {
                    "$regex": pattern,
                    "$options": "i",
                }
            },
            projection,
        ).limit(limit * 2)

        return [self._build_chunk_result(doc) for doc in cursor]

    def search_by_any_keywords(
        self,
        keywords: List[str],
        limit: int = 5,
        return_strategy: bool = False,
        doc_ids: Optional[List[Any]] = None,
        source_documents: Optional[List[str]] = None,
    ) -> Union[List[Dict[str, Any]], Tuple[List[Dict[str, Any]], str]]:
        """
        使用多个关键词（OR）搜索文档块（忽略大小写，模糊匹配）
        """
        cleaned_terms = [kw.strip() for kw in keywords if kw and kw.strip()]
        if not cleaned_terms:
            print("[WARN] MongoDB搜索：关键词列表为空")
            return ([], "none") if return_strategy else []

        try:
            # 构建正则表达式模式（OR 逻辑）
            pattern = "|".join(re.escape(term) for term in cleaned_terms)
            if not pattern:
                print("[WARN] MongoDB搜索：正则模式为空")
                return ([], "none") if return_strategy else []

            print(f"[MongoDB] 开始搜索，关键词={cleaned_terms}, 正则模式={pattern[:100]}...")

            used_strategy = "regex_or"
            chunks: List[Dict[str, Any]] = []

            if self._text_index_enabled:
                try:
                    text_chunks = self._search_with_text_index(
                        cleaned_terms,
                        limit,
                        doc_ids=doc_ids,
                        source_documents=source_documents,
                    )
                    if text_chunks:
                        used_strategy = "text_index"
                        chunks = text_chunks
                except Exception as exc:
                    print(f"[WARN] MongoDB 文本索引搜索失败，回退正则: {exc}")

            if not chunks:
                chunks = self._search_with_regex(
                    pattern,
                    limit,
                    doc_ids=doc_ids,
                    source_documents=source_documents,
                )

            print(f"[MongoDB] 搜索完成：找到 {len(chunks)} 个匹配的chunks（策略：{used_strategy}）")

            result = chunks[:limit]
            if return_strategy:
                return result, (used_strategy if result else "none")
            return result

        except Exception as exc:
            print(f"[ERR] MongoDB关键词搜索失败: {exc}")
            import traceback
            traceback.print_exc()
            return ([], "error") if return_strategy else []

    def smart_keyword_search(
        self,
        keywords: List[str],
        fallback_query: Optional[str],
        limit: int = 5,
        doc_ids: Optional[List[Any]] = None,
        source_documents: Optional[List[str]] = None,
    ) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
        """
        综合使用全文索引/正则/原始查询的搜索策略。
        返回: (chunks, used_strategy, diagnostics)
        """
        diagnostics: Dict[str, Any] = {"attempts": []}

        cleaned_terms = [kw.strip() for kw in keywords if kw and kw.strip()]
        if cleaned_terms:
            diagnostics["attempts"].append("search_terms")
            chunks, strategy = self.search_by_any_keywords(
                cleaned_terms,
                limit=limit,
                return_strategy=True,
                doc_ids=doc_ids,
                source_documents=source_documents,
            )
            if chunks:
                return chunks, strategy, diagnostics

        if fallback_query:
            diagnostics["attempts"].append("fallback_query")
            chunks = self.search_by_keywords(
                fallback_query,
                limit,
                doc_ids=doc_ids,
                source_documents=source_documents,
            )
            if chunks:
                return chunks, "fallback_query", diagnostics

        return [], "none", diagnostics
    
    def format_chunks_for_display(
        self,
        chunks: List[Dict[str, Any]],
        max_text_length: int = 500
    ) -> str:
        """
        将 chunks 格式化为用户友好的文本
        
        Args:
            chunks: chunk 列表
            max_text_length: 单个 chunk 文本的最大显示长度
        
        Returns:
            格式化的文本输出
        """
        if not chunks:
            return "未找到相关的文档块。"
        
        lines = [f"[MongoDB检索] 找到 {len(chunks)} 个相关文档块：\n"]
        
        for i, chunk in enumerate(chunks, 1):
            chunk_id = chunk.get("chunk_id", "unknown")
            source = chunk.get("source_document", "未知来源")
            text = chunk.get("chunk_text", "")
            metadata = chunk.get("metadata", {})
            
            # 截取文本
            if len(text) > max_text_length:
                display_text = text[:max_text_length] + "..."
            else:
                display_text = text
            
            # 提取有用的元数据
            meta_info = []
            if isinstance(metadata, dict):
                if "page" in metadata:
                    meta_info.append(f"第{metadata['page']}页")
                if "section" in metadata:
                    meta_info.append(f"{metadata['section']}")
            
            meta_str = ", ".join(meta_info) if meta_info else "无元数据"
            
            lines.append(f"\n### Chunk {i}")
            lines.append(f"**来源**: {source}")
            lines.append(f"**位置**: {meta_str}")
            lines.append(f"**Chunk ID**: `{chunk_id}`")
            lines.append(f"\n**内容**:\n{display_text}")
            lines.append("\n" + "-" * 60)
        
        lines.append("\n**数据来源**: MongoDB Document Database (medical_chunks)")
        
        return "\n".join(lines)
    
    def close(self):
        """关闭 MongoDB 连接"""
        try:
            self.client.close()
        except:
            pass


# 全局实例
_retriever = None


def get_retriever() -> MongoDBChunkRetriever:
    """获取全局检索器实例（单例模式）"""
    global _retriever
    if _retriever is None:
        _retriever = MongoDBChunkRetriever()
    return _retriever


# ========================================
# LangChain Tool 封装
# ========================================

@tool("mongodb_chunk_retrieval")
def mongodb_chunk_retrieval(chunk_ids: str) -> str:
    """
    从MongoDB检索完整的文档块内容。
    
    适用场景：
    - 根据 chunk_id 获取完整文本
    - 补充图谱和向量检索的详细信息
    - 获取原始文档的上下文
    
    输入参数：
    - chunk_ids: 逗号分隔的 chunk ID 列表（如"chunk1,chunk2,chunk3"）
    
    输出：
    返回完整的 chunk 文本，包含：
    - 来源文档和位置信息
    - 完整的文本内容
    - 元数据（页码、章节等）
    """
    try:
        # 解析 chunk_ids（逗号分隔）
        chunk_id_list = [cid.strip() for cid in chunk_ids.split(",") if cid.strip()]
        
        if not chunk_id_list:
            return "请提供有效的 chunk_id 列表（逗号分隔）。"
        
        retriever = get_retriever()
        chunks = retriever.get_chunks_by_ids(chunk_id_list)
        
        return retriever.format_chunks_for_display(chunks)
        
    except Exception as e:
        return f"MongoDB检索失败：{e}"


@tool("mongodb_keyword_search")
def mongodb_keyword_search(keywords: str, limit: int = 5) -> str:
    """
    在MongoDB中根据关键词搜索相关文档块。
    
    适用场景：
    - 当没有 chunk_id 时进行文本搜索
    - 探索性查询
    - 补充其他检索结果
    
    输入参数：
    - keywords: 搜索关键词
    - limit: 返回结果数量（默认5）
    
    输出：
    返回匹配的文档块列表，按相关度排序。
    """
    try:
        retriever = get_retriever()
        chunks = retriever.search_by_keywords(keywords, limit)
        
        return retriever.format_chunks_for_display(chunks)
        
    except Exception as e:
        return f"MongoDB搜索失败：{e}"


# 导出工具列表
tools = [
    mongodb_chunk_retrieval,
    mongodb_keyword_search
]


# ========================================
# 测试代码
# ========================================

if __name__ == "__main__":
    print("=" * 80)
    print("MongoDB 文档块检索工具测试")
    print("=" * 80)
    print()
    
    retriever = get_retriever()
    
    # 测试 1: 关键词搜索
    print("\n测试 1: 关键词搜索 '急诊部 抢救室'")
    print("-" * 80)
    result = mongodb_keyword_search.invoke({
        "keywords": "急诊部 抢救室",
        "limit": 3
    })
    print(result)
    
    # 测试 2: 获取统计信息
    print("\n" + "=" * 80)
    print("数据库统计信息")
    print("=" * 80)
    total_chunks = retriever.chunks_collection.count_documents({})
    print(f"总chunk数: {total_chunks}")
    
    # 按来源文档分组统计
    pipeline = [
        {"$group": {
            "_id": "$doc_title",
            "count": {"$sum": 1}
        }},
        {"$sort": {"count": -1}},
        {"$limit": 5}
    ]
    
    print("\nTop 5 文档（按chunk数量）:")
    for doc in retriever.chunks_collection.aggregate(pipeline):
        doc_title = doc['_id'] or "未知"
        print(f"  - {doc_title}: {doc['count']} chunks")
    
    retriever.close()

