# app/services/rag_store.py
from __future__ import annotations
import os
import pathlib
import hashlib
import shutil
from typing import List, Tuple, Iterable, Dict, Any
import json
import requests
from dotenv import load_dotenv
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
import threading

# LangChain 相关组件

from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from langchain_chroma import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_openai import OpenAIEmbeddings

# PDF 处理与 ChromaDB 客户端
from pypdf import PdfReader
import chromadb
import time

# --- 1. 全局配置 ---

# 优先加载项目根目录的 .env，便于离线脚本直接读取密钥
load_dotenv()

# 配置常量
PERSIST_DIR = os.path.join("databases", "vector", "chroma")
os.makedirs(PERSIST_DIR, exist_ok=True)

# OCR 缓存目录（基于文件内容指纹）
OCR_CACHE_DIR = os.path.join(PERSIST_DIR, "ocr_cache")
os.makedirs(OCR_CACHE_DIR, exist_ok=True)

# 索引清单：用于跳过已处理且未变化的文件
INDEX_MANIFEST = os.path.join(PERSIST_DIR, "index_manifest.json")

def _load_manifest() -> Dict[str, Any]:
    try:
        if os.path.exists(INDEX_MANIFEST):
            with open(INDEX_MANIFEST, "r", encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception as e:
        print(f"警告: 读取索引清单失败: {e}")
    return {}

def _save_manifest(manifest: Dict[str, Any]) -> None:
    try:
        with open(INDEX_MANIFEST, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"警告: 写入索引清单失败: {e}")

SUPPORTED_SUFFIX = {".txt", ".md", ".markdown", ".pdf"}
DEFAULT_COLLECTION = "local_docs"

# 支持的 Embedding 模型枚举
class EmbeddingModel(Enum):
    BGE_SMALL_ZH = "BAAI/bge-small-zh-v1.5"
    BGE_LARGE_ZH = "BAAI/bge-large-zh-v1.5"
    BGE_M3 = "BAAI/bge-m3"  # 多语言模型
    OPENAI_ADA = "text-embedding-ada-002"
    OPENAI_3_SMALL = "text-embedding-3-small"
    OPENAI_3_LARGE = "text-embedding-3-large"

@dataclass
class RAGConfig:
    """RAG 配置类，用于集中管理所有参数"""
    embedding_model: str = EmbeddingModel.OPENAI_3_LARGE.value
    chunk_size: int = 800
    chunk_overlap: int = 120
    max_workers: int = 4
    enable_ocr: bool = False
    prefer_ocr: bool = False
    enable_rerank: bool = False
    collection_name: str = DEFAULT_COLLECTION

# 创建一个全局配置实例
_config = RAGConfig()

def configure_rag(**kwargs) -> None:
    """
    配置全局 RAG 参数。
    调用此函数可以动态修改 RAG 系统的行为。
    """
    global _config
    for key, value in kwargs.items():
        if hasattr(_config, key):
            setattr(_config, key, value)
    # 清理缓存以确保新配置生效
    _get_embedding.cache_clear()
    _get_db.cache_clear()
    _get_reranker.cache_clear()
    print(f"RAG 配置已更新: {kwargs}")

# --- 2. 核心组件（模型、数据库、重排序器） ---

@lru_cache(maxsize=1)
def _get_embedding():
    """优先使用 OpenAI Embeddings；失败或无 API_KEY 时回退 HuggingFace BGE-small-zh。"""
    model_name = os.getenv("RAG_EMBED_MODEL", EmbeddingModel.OPENAI_3_LARGE.value)
    api_key = "sk-NbZ9AEWhhFOVPIeI46C9980859234dD88b3c01A14dAfAd12"
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE")

    if api_key:
        try:
            print(f"正在加载 OpenAI Embedding: {model_name} (base={base_url or 'default'})")
            return OpenAIEmbeddings(
                model=model_name,
                api_key=api_key,
                base_url=base_url,  # 最新 SDK 统一用 base_url
                dimensions=1536,  # 使用完整的1536维
            )
        except Exception as e:
            print(f"⚠️ OpenAI Embedding 初始化失败，回退到 BGE-small-zh。原因: {e}")

    print("⚠️ 未使用 OpenAI（缺少 OPENAI_API_KEY 或初始化失败），回退到 BGE-small-zh")
    return HuggingFaceEmbeddings(model_name=EmbeddingModel.BGE_SMALL_ZH.value)


@lru_cache(maxsize=4)  # 缓存多个 collection 的连接
def _get_db(collection_name: str = None):
    """获取并缓存向量数据库实例"""
    collection = collection_name or _config.collection_name
    return Chroma(
        persist_directory=PERSIST_DIR,
        embedding_function=_get_embedding(),
        collection_name=collection,
    )

@lru_cache(maxsize=1)
def _get_reranker():
    """加载并缓存重排序（reranker）模型"""
    if not _config.enable_rerank:
        return None
    try:
        from sentence_transformers import CrossEncoder
        print("正在加载 reranker 模型: BAAI/bge-reranker-base...")
        return CrossEncoder('BAAI/bge-reranker-base')
    except ImportError:
        print("警告: 依赖库 `sentence_transformers` 未安装，重排序功能已禁用。")
        return None
    except Exception as e:
        print(f"错误: 加载 reranker 模型失败: {e}")
        return None

def _get_text_splitter():
    """根据全局配置创建文本分割器"""
    return RecursiveCharacterTextSplitter(
        chunk_size=_config.chunk_size,
        chunk_overlap=_config.chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", "，", " ", ""]
    )

# --- 3. 文件处理与内容提取 ---

def _read_text_file(fp: str) -> str:
    """读取文本文件"""
    try:
        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception as e:
        print(f"错误: 读取文件 {fp} 失败: {e}")
        return ""

def _ocr_pdf(fp: str) -> str:
    """使用 TextIn v1 pdf_to_markdown API 从 PDF 提取 Markdown 文本。

    环境变量：
    - TEXTIN_APP_ID: 必填
    - TEXTIN_SECRET_CODE: 优先，其次兼容 TEXTIN_SECRET_KEY
    - TEXTIN_OCR_OPTIONS: 可选，JSON 字符串，作为 query params 传入
    """
    api_url = "https://api.textin.com/ai/service/v1/pdf_to_markdown"

    app_id = os.getenv("TEXTIN_APP_ID")
    secret_code = os.getenv("TEXTIN_SECRET_CODE") or os.getenv("TEXTIN_SECRET_KEY")
    if not app_id or not secret_code:
        print("警告: TEXTIN_APP_ID 或 TEXTIN_SECRET_CODE 未设置，跳过 OCR。")
        return ""

    # 可选参数，支持通过环境变量传入 JSON
    params: Dict[str, Any] = {}
    raw_opts = os.getenv("TEXTIN_OCR_OPTIONS")
    if raw_opts:
        try:
            parsed = json.loads(raw_opts)
            if isinstance(parsed, dict):
                # TextIn 要求所有值为字符串
                params = {k: str(v) for k, v in parsed.items()}
        except Exception as e:
            print(f"警告: 无法解析 TEXTIN_OCR_OPTIONS: {e}")

    headers = {
        "x-ti-app-id": app_id,
        "x-ti-secret-code": secret_code,
        "Content-Type": "application/octet-stream",
    }

    print(f"信息: 使用 TextIn v1 OCR 解析: {os.path.basename(fp)} …")
    try:
        with open(fp, "rb") as f:
            file_bytes = f.read()
        resp = requests.post(api_url, params=params, headers=headers, data=file_bytes, timeout=120)
        resp.raise_for_status()

        # v1 返回通常为 markdown 文本；若为 JSON 则尝试取 markdown 字段
        content_type = resp.headers.get("Content-Type", "").lower()
        if "application/json" in content_type:
            data = resp.json()
            md = data.get("result", {}).get("markdown") if isinstance(data, dict) else None
            if md and isinstance(md, str) and md.strip():
                return md
            # 回退到整体文本
            return json.dumps(data, ensure_ascii=False)
        else:
            text = resp.text or ""
            return text
    except requests.exceptions.RequestException as e:
        print(f"错误: TextIn OCR 请求失败: {e}")
    except Exception as e:
        print(f"错误: TextIn OCR 处理异常: {e}")
    return ""

def _file_md5(fp: str) -> str:
    """计算文件内容 MD5 指纹（用于 OCR 缓存键）。"""
    h = hashlib.md5()
    try:
        with open(fp, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        print(f"警告: 计算文件指纹失败 {fp}: {e}")
        # 退化到路径+mtime 指纹
        st = os.stat(fp)
        h.update(f"{fp}|{st.st_mtime}".encode("utf-8"))
        return h.hexdigest()

def _ocr_pdf_cached(fp: str) -> str:
    """带缓存的 OCR：优先命中缓存，否则请求后写入缓存。"""
    try:
        key = _file_md5(fp)
        cache_fp = os.path.join(OCR_CACHE_DIR, f"{key}.md")
        if os.path.exists(cache_fp):
            try:
                with open(cache_fp, "r", encoding="utf-8", errors="ignore") as f:
                    cached = f.read()
                if cached and cached.strip():
                    return cached
            except Exception as e:
                print(f"警告: 读取 OCR 缓存失败 {cache_fp}: {e}")

        text = _ocr_pdf(fp)
        if text and text.strip():
            try:
                with open(cache_fp, "w", encoding="utf-8") as f:
                    f.write(text)
            except Exception as e:
                print(f"警告: 写入 OCR 缓存失败 {cache_fp}: {e}")
        return text
    except Exception as e:
        print(f"错误: OCR 缓存流程异常 {fp}: {e}")
        return _ocr_pdf(fp)

def _read_pdf_file(fp: str) -> str:
    """按策略抽取 PDF 文本：
    - prefer_ocr=True 且 enable_ocr=True: 优先 OCR，失败再 pypdf
    - 否则：先 pypdf，抽不到且 enable_ocr=True 时再 OCR
    """
    def extract_by_pypdf() -> str:
        pages = []
        try:
            reader = PdfReader(fp)
            for page in reader.pages:
                text = (page.extract_text() or "").strip()
                if text:
                    pages.append(text)
        except Exception as e:
            print(f"错误: 读取 PDF 文件 {fp} 失败: {e}")
        return "\n".join(pages).strip()

    # 路径1：优先 OCR
    if _config.enable_ocr and _config.prefer_ocr:
        ocr_md = _ocr_pdf_cached(fp)
        if ocr_md and ocr_md.strip():
            return ocr_md.strip()
        # 回退 pypdf
        return extract_by_pypdf()

    # 路径2：先 pypdf
    text = extract_by_pypdf()
    if text:
        return text
    if _config.enable_ocr:
        ocr_md = _ocr_pdf_cached(fp)
        if ocr_md and ocr_md.strip():
            return ocr_md.strip()
    return ""

def _load_file(fp: str) -> Tuple[str, str]:
    """根据文件类型加载单个文件"""
    suffix = pathlib.Path(fp).suffix.lower()
    try:
        if suffix in {".txt", ".md", ".markdown"}:
            return fp, _read_text_file(fp)
        elif suffix == ".pdf":
            return fp, _read_pdf_file(fp)
        else:
            print(f"警告: 不支持的文件类型: {fp}")
            return fp, ""
    except Exception as e:
        print(f"错误: 加载文件 {fp} 失败: {e}")
        return fp, ""

def _parallel_load_files(files: List[str]) -> List[Tuple[str, str]]:
    """使用线程池并行加载多个文件"""
    results = []
    try:
        with ThreadPoolExecutor(max_workers=_config.max_workers) as executor:
            future_to_file = {executor.submit(_load_file, fp): fp for fp in files}
            for future in as_completed(future_to_file):
                try:
                    results.append(future.result())
                except Exception as e:
                    fp = future_to_file[future]
                    print(f"错误: 并行加载 {fp} 时出现异常: {e}")
                    results.append((fp, ""))
    except RuntimeError as e:
        if "cannot schedule new futures after interpreter shutdown" in str(e):
            print("警告: 解释器关闭，回退到串行加载")
            # 回退到串行加载
            for fp in files:
                try:
                    results.append(_load_file(fp))
                except Exception as e:
                    print(f"错误: 串行加载 {fp} 失败: {e}")
                    results.append((fp, ""))
        else:
            raise
    return results

def _gather_files(paths: Iterable[str]) -> List[str]:
    """从给定路径递归收集所有支持的文件"""
    gathered = []
    for p in paths:
        if not p: continue
        p = os.path.abspath(p)
        if os.path.isdir(p):
            for root, _, files in os.walk(p):
                for name in files:
                    if pathlib.Path(name).suffix.lower() in SUPPORTED_SUFFIX:
                        gathered.append(os.path.join(root, name))
        elif os.path.isfile(p) and pathlib.Path(p).suffix.lower() in SUPPORTED_SUFFIX:
            gathered.append(p)
    
    unique_files = sorted(set(gathered))
    print(f"信息: 共收集到 {len(unique_files)} 个文件。")
    return unique_files

def _chunk_docs(fp: str, content: str) -> List[Document]:
    """将单个文件的内容分割成多个文档块 (Document)"""
    if not content:
        return []
    
    splitter = _get_text_splitter()
    chunks = splitter.split_text(content)
    docs: List[Document] = []
    
    for i, ch in enumerate(chunks):
        if not ch.strip(): continue
        docs.append(
            Document(
                page_content=ch,
                metadata={"source": fp, "chunk_id": i, "filename": os.path.basename(fp)}
            )
        )
    return docs

def _make_id(source: str, chunk_id: int, text: str) -> str:
    """为每个文档块生成一个唯一的、可复现的 ID"""
    h = hashlib.md5()
    h.update(source.encode("utf-8"))
    h.update(str(chunk_id).encode("utf-8"))
    h.update(text.encode("utf-8"))
    return h.hexdigest()

# --- 4. 数据库管理与索引 ---

@lru_cache(maxsize=1)
def _get_chroma_client():
    """获取并缓存 ChromaDB 的原始客户端"""
    return chromadb.PersistentClient(path=PERSIST_DIR)

def reset_db(collection_name: str = None):
    """重置（删除）指定的 collection"""
    collection = collection_name or _config.collection_name
    try:
        client = _get_chroma_client()
        client.delete_collection(name=collection)
        _get_db.cache_clear()
        print(f"信息: Collection '{collection}' 已被成功删除。")
    except ValueError:
        print(f"警告: Collection '{collection}' 不存在，无需删除。")
    except Exception as e:
        print(f"错误: 删除 collection '{collection}' 失败: {e}")

def index_files(files: List[str], collection_name: str = None) -> Tuple[int, int]:
    """将文件列表内容批量索引到数据库"""
    valid_files = [f for f in files if f and os.path.exists(f)]
    if not valid_files:
        print("警告: 没有有效的文件可供索引。")
        return 0, 0

    # 读取索引清单，按文件内容指纹与配置跳过未变化项
    manifest = _load_manifest()
    current_sig = {
        "embedding_model": os.getenv("RAG_EMBED_MODEL", EmbeddingModel.OPENAI_3_LARGE.value),
        "chunk_size": _config.chunk_size,
        "chunk_overlap": _config.chunk_overlap,
        "collection": collection_name or _config.collection_name,
    }

    file_fps: List[Tuple[str, str]] = []  # (fp, md5)
    to_process: List[str] = []
    skipped: List[str] = []
    for fp in valid_files:
        md5v = _file_md5(fp)
        file_fps.append((fp, md5v))
        rec = manifest.get(fp)
        if rec and rec.get("md5") == md5v and rec.get("signature") == current_sig:
            skipped.append(fp)
        else:
            to_process.append(fp)

    if skipped:
        print(f"信息: 跳过 {len(skipped)} 个未变化文件（基于清单）。")

    if not to_process:
        return len(valid_files), 0

    print(f"信息: 开始并行加载 {len(to_process)} 个文件...")
    file_contents = _parallel_load_files(to_process)
    
    all_docs: List[Document] = []
    ids: List[str] = []
    
    for fp, content in file_contents:
        if not content: continue
        docs = _chunk_docs(fp, content)
        for d in docs:
            cid = int(d.metadata.get("chunk_id", 0))
            ids.append(_make_id(d.metadata.get("source", ""), cid, d.page_content))
        all_docs.extend(docs)

    if not all_docs:
        print("警告: 未能从文件中生成任何可索引的文档块。")
        return 0, 0

    db = _get_db(collection_name)
    try:
        # 预取已存在的ID，过滤重复写入
        try:
            existing = set(db.get(ids=None).get("ids", []))
        except Exception:
            existing = set()
        new_docs: List[Document] = []
        new_ids: List[str] = []
        for doc, _id in zip(all_docs, ids):
            if _id not in existing:
                new_docs.append(doc)
                new_ids.append(_id)

        if not new_docs:
            print("信息: 全部片段已存在，跳过写入。")
            return len(valid_files), 0

        db.add_documents(new_docs, ids=new_ids)
        # 新版 ChromaDB 会自动持久化，db.persist() 通常不再需要
        print(f"信息: 成功索引 {len(new_docs)} 个文档块（来自 {len(to_process)} 个文件）。")

        # 更新索引清单（仅标注处理过的文件）
        now_ts = int(time.time())
        for fp, md5v in file_fps:
            if fp in to_process:
                manifest[fp] = {
                    "md5": md5v,
                    "signature": current_sig,
                    "last_indexed": now_ts,
                }
        _save_manifest(manifest)
    except Exception as e:
        print(f"错误: 文档索引失败: {e}")
        raise

    return len(valid_files), len(new_docs)

def index_paths(paths: List[str], *, reset: bool = False, collection_name: str = None) -> Tuple[int, int, int]:
    """扫描指定路径并索引文件，返回（文件数，片段数，实际新增片段数）"""
    collection = collection_name or _config.collection_name
    if reset:
        reset_db(collection)
    
    gathered = _gather_files(paths)
    before = get_count(collection)
    files_n, chunks_n = index_files(gathered, collection)
    after = get_count(collection)
    
    actual_written = max(0, after - before)
    print(f"索引完成: 处理 {files_n} 个文件, 生成 {chunks_n} 个片段, 新增 {actual_written} 个片段。")
    
    return files_n, chunks_n, actual_written

# 便捷：索引整个 databases/documents 目录
def index_all_documents(*, reset: bool = False, collection_name: str = None) -> Tuple[int, int, int]:
    root_dir = os.path.join("databases", "documents")
    return index_paths([root_dir], reset=reset, collection_name=collection_name)

# --- 5. 数据检索与查询 ---

def get_count(collection_name: str = None) -> int:
    """获取指定 collection 中的文档块总数"""
    collection = collection_name or _config.collection_name
    try:
        db = _get_db(collection)
        return len(db.get().get("ids", []))
    except Exception as e:
        print(f"错误: 获取文档数量失败: {e}")
        return 0

def _rerank_results(query: str, docs: List[Document]) -> List[Document]:
    """对初步检索结果进行重排序"""
    model = _get_reranker()
    if not model or not docs:
        return docs
    
    try:
        pairs = [[query, doc.page_content] for doc in docs]
        scores = model.predict(pairs)
        
        # 将文档和分数打包排序
        sorted_docs = [doc for _, doc in sorted(zip(scores, docs), key=lambda x: x[0], reverse=True)]
        
        print(f"信息: 已对 {len(docs)} 个文档进行重排序。")
        return sorted_docs
    except Exception as e:
        print(f"错误: 重排序失败: {e}")
        return docs

def similarity_search(
    query: str, 
    k: int = 5, 
    collection_name: str = None, 
    filter: Dict[str, Any] = None
) -> List[Document]:
    """执行语义相似度搜索"""
    try:
        db = _get_db(collection_name)
        
        # 如果启用重排序，获取更多候选文档
        fetch_k = k * 4 if _config.enable_rerank else k
        
        docs = db.similarity_search(query, k=fetch_k, filter=filter)
        
        if _config.enable_rerank:
            docs = _rerank_results(query, docs)
            
        return docs[:k]
    except Exception as e:
        print(f"错误: 搜索失败: {e}")
        return []

def rag_preview(
    query: str, 
    k: int = 5, 
    collection_name: str = None
) -> str:
    """格式化并预览 RAG 检索结果"""
    docs = similarity_search(query, k, collection_name)
    
    if not docs:
        return "本地知识库暂无匹配内容。"
    
    lines = [f"Top {k} 相似片段 (重排序: {'启用' if _config.enable_rerank else '禁用'}):"]
    for i, d in enumerate(docs, 1):
        filename = d.metadata.get("filename", "未知文件")
        content = d.page_content.strip().replace("\n", " ")
        snippet = content[:400] + ("..." if len(content) > 400 else "")
        lines.append(f"{i}. 文件: {filename}\n   片段: {snippet}")
    
    return "\n".join(lines)

# --- 6. 知识库状态与管理 ---

def list_collections() -> List[str]:
    """列出数据库中所有存在的 collection"""
    try:
        client = _get_chroma_client()
        return [c.name for c in client.list_collections()]
    except Exception as e:
        print(f"错误: 列出 collections 失败: {e}")
        return []

def get_stats(collection_name: str = None) -> Dict[str, Any]:
    """获取指定 collection 的统计信息"""
    collection = collection_name or _config.collection_name
    try:
        db = _get_db(collection)
        data = db.get(include=["metadatas"])
        
        sources = {meta.get("source") for meta in data.get("metadatas", []) if meta}
        
        return {
            "collection": collection,
            "总片段数": len(data.get("ids", [])),
            "总文件数": len(sources),
            "embedding_model": _config.embedding_model,
            "chunk_size": _config.chunk_size,
        }
    except Exception as e:
        print(f"错误: 获取统计信息失败: {e}")
        return {"collection": collection, "error": str(e)}

def list_sources(collection_name: str = None) -> List[Dict[str, Any]]:
    """列出来源文件及其片段数；若数据库为空或失败，则回退扫描文件系统。"""
    collection = collection_name or _config.collection_name
    try:
        db = _get_db(collection)
        # Chroma 不支持 include "ids"；此处仅拉取 metadatas 即可完成计数
        data = db.get(include=["metadatas"])  # type: ignore
        metadatas = data.get("metadatas", []) or []
        counter: Dict[str, Dict[str, Any]] = {}
        for meta in metadatas:
            if not meta:
                continue
            src = meta.get("source") or ""
            fname = meta.get("filename") or os.path.basename(src) or "未知文件"
            if src not in counter:
                counter[src] = {"source": src, "filename": fname, "chunks": 0}
            counter[src]["chunks"] += 1
        items = list(counter.values())
        if items:
            items.sort(key=lambda x: x["filename"].lower())
            return items
        # 若向量库还没有任何数据，回退到文件系统列出候选文件（chunks 置 0）
        root_dir = os.path.join("databases", "documents")
        files = _gather_files([root_dir])
        fallback = [{"source": f, "filename": os.path.basename(f), "chunks": 0} for f in files]
        fallback.sort(key=lambda x: x["filename"].lower())
        return fallback
    except Exception as e:
        print(f"错误: 列出来源失败: {e}")
        # 异常时同样回退到文件系统
        try:
            root_dir = os.path.join("databases", "documents")
            files = _gather_files([root_dir])
            fallback = [{"source": f, "filename": os.path.basename(f), "chunks": 0} for f in files]
            fallback.sort(key=lambda x: x["filename"].lower())
            return fallback
        except Exception:
            return []

def describe_knowledgebase(collection_name: str = None) -> str:
    """返回知识库简要说明（用于给智能体感知资料范围与文件名）。"""
    stats = get_stats(collection_name)
    items = list_sources(collection_name)
    if not items:
        return f"集合 {stats.get('collection')} 目前为空。可将资料放入 databases/documents 后重启。"
    lines = [
        f"集合: {stats.get('collection')}",
        f"文件数: {len(items)}，片段数: {stats.get('总片段数', 0)}",
        "文件清单：",
    ]
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. {it['filename']} (chunks={it['chunks']})")
    return "\n".join(lines)

def startup_preprocess(*, paths: List[str] = None, reset: bool = False, collection_name: str = None, async_mode: bool = True):
    """在应用启动后触发预处理：扫描并索引 documents。

    - paths 为空时默认扫描 databases/documents
    - async_mode=True 时后台线程执行，不阻塞主线程
    """
    targets = paths or [os.path.join("databases", "documents")]

    def _run():
        try:
            print("[RAG] 启动期预处理开始：扫描并索引资料…")
            files_n, chunks_n, written_n = index_paths(targets, reset=reset, collection_name=collection_name)
            print(f"[RAG] 启动期预处理完成：文件 {files_n}，片段 {chunks_n}，新增 {written_n}。")
        except Exception as e:
            print(f"[RAG] 启动期预处理失败: {e}")

    if async_mode:
        try:
            t = threading.Thread(target=_run, daemon=True)
            t.start()
            return t
        except RuntimeError as e:
            if "cannot schedule new futures after interpreter shutdown" in str(e):
                print("[RAG] 解释器正在关闭，跳过异步预处理")
                return None
            else:
                raise
    else:
        _run()
        return None



    
if __name__ == "__main__":
    # 简单自测：查询“现带医疗体系是什么”（按用户要求）
    # 1) 可选：先确保本地向量库有内容（如需要可取消注释进行索引）
    # 示例：索引 databases/documents 目录下的支持文件
    # files_n, chunks_n, written_n = index_paths(["databases/documents"], reset=False)
    # print(f"索引统计 => 文件: {files_n}, 片段: {chunks_n}, 新增: {written_n}")

    # 若需先为默认集合构建索引（整个 documents），取消注释：
    # configure_rag(collection_name="local_docs", enable_ocr=False)
    # print(index_all_documents(reset=False, collection_name="local_docs"))

    question = "现带医疗体系是什么"
    print("\n===== RAG 预览自测 =====")
    print(f"问题: {question}")
    print(rag_preview(question, k=5))
    print("===== 自测结束 =====\n")