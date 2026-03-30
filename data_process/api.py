"""
FastAPI 路由 -- data_process API + WebSocket 进度推送
"""

import asyncio
import os
import uuid
import shutil
import logging
import json
import re
import threading
import time
import random
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta, timezone

from fastapi import (
    APIRouter, WebSocket, WebSocketDisconnect,
    UploadFile, File, Form, HTTPException, BackgroundTasks, Query,
)

from data_process.schemas import (
    OcrRequest, OcrResultResponse,
    VectorizeRequest, VectorizeFromOcrRequest, VectorizeResultResponse,
    RerankRequest, RerankResultResponse,
    KgBuildRequest, KgStageOnlyRequest,
    TaskResponse, TaskStatusResponse, TaskStatus,
    ProgressUpdate, UploadedFileInfo,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/data-process", tags=["Data Processing"])

# ============================================================
# 任务存储 (内存版，生产环境可换 Redis)
# ============================================================
_tasks: Dict[str, Dict[str, Any]] = {}
_ws_connections: Dict[str, WebSocket] = {}
_tasks_lock = threading.RLock()

UPLOAD_DIR = Path(os.getenv("DATA_PROCESS_UPLOAD_DIR", "data_process/uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

DOCUMENTS_DIR = Path(os.getenv("DATA_PROCESS_DOCUMENTS_DIR", "data_process/documents"))
DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)

DOCUMENTS_OCR_DIR = Path(os.getenv("DATA_PROCESS_OCR_DIR", "data_process/documents_ocr"))
DOCUMENTS_OCR_DIR.mkdir(parents=True, exist_ok=True)

CATEGORIES = ["标准规范", "参考论文", "书籍报告", "政策文件"]
TASKS_STORE_FILE = Path(os.getenv("DATA_PROCESS_TASKS_FILE", "data_process/tasks_state.json"))
try:
    OCR_MAX_CONCURRENT = max(1, int(os.getenv("DATA_PROCESS_OCR_MAX_CONCURRENT", "2")))
except Exception:
    OCR_MAX_CONCURRENT = 2
_ocr_semaphore = threading.BoundedSemaphore(OCR_MAX_CONCURRENT)
_reranker_lock = threading.Lock()
_reranker_instance = None
_ocr_artifacts_cache_lock = threading.Lock()
_ocr_artifacts_cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
try:
    OCR_ARTIFACT_CACHE_SIZE = max(
        0, int(os.getenv("DATA_PROCESS_OCR_ARTIFACT_CACHE_SIZE", "512"))
    )
except Exception:
    OCR_ARTIFACT_CACHE_SIZE = 512

try:
    VECTOR_RETRY_MAX_DELAY_SEC = max(
        1, int(os.getenv("DATA_PROCESS_VECTOR_RETRY_MAX_DELAY_SEC", "60"))
    )
except Exception:
    VECTOR_RETRY_MAX_DELAY_SEC = 60

VECTOR_FAILURE_LOG_FILE = Path(
    os.getenv("DATA_PROCESS_VECTOR_FAILURE_LOG", "data_process/vector_failure.log")
)

_vector_worker_lock = threading.Lock()
_vector_workers: Dict[str, threading.Thread] = {}
_kg_worker_lock = threading.Lock()
_kg_workers: Dict[str, threading.Thread] = {}


def _is_probably_absolute_path(p: str) -> bool:
    # Windows drive letter, UNC, or Unix-style absolute.
    return bool(re.match(r"^[a-zA-Z]:[\\/]", p)) or p.startswith("\\\\") or p.startswith("/")


def _norm_doc_path(p: str) -> str:
    # Normalize to forward slashes so API is stable across OSes.
    p = (p or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _doc_path_for(cat: str, filename: str) -> str:
    return f"{cat}/{filename}".replace("\\", "/")


def _to_relative_ocr_dir(artifacts_dir: Optional[str]) -> Optional[str]:
    if not artifacts_dir:
        return None
    try:
        p = Path(str(artifacts_dir)).resolve()
        rel = p.relative_to(DOCUMENTS_OCR_DIR.resolve())
        return str(rel).replace("\\", "/")
    except Exception:
        return None


def _resolve_pdf_under_documents(file_path: str, category: str = "") -> tuple[str, str, Path]:
    """Resolve request path to an absolute PDF path under DOCUMENTS_DIR.

    Accepts either:
    - relative doc_path: "<category>/<filename>.pdf" (preferred)
    - absolute path: must be located under DOCUMENTS_DIR (legacy/compat)

    Returns: (doc_path, category, abs_path)
    """
    if not file_path or not isinstance(file_path, str):
        raise HTTPException(400, "file_path is required")

    raw = _norm_doc_path(file_path)

    if _is_probably_absolute_path(raw):
        abs_p = Path(file_path).resolve()
        try:
            rel = abs_p.relative_to(DOCUMENTS_DIR.resolve())
        except Exception:
            raise HTTPException(400, "file_path must be under documents root")
        rel_parts = list(rel.parts)
        if len(rel_parts) < 2:
            raise HTTPException(400, "file_path must include category and filename")
        cat = str(rel_parts[0])
        doc_path = _doc_path_for(cat, str(Path(*rel_parts[1:])))
    else:
        rel = Path(raw)
        if rel.is_absolute():
            raise HTTPException(400, "invalid file_path")
        if any(part in ("..", "") for part in rel.parts):
            raise HTTPException(400, "invalid file_path")

        parts = list(rel.parts)
        if len(parts) == 1:
            if not category:
                raise HTTPException(400, "file_path must include category, or provide category explicitly")
            cat = category
            doc_path = _doc_path_for(cat, parts[0])
        else:
            cat = parts[0]
            doc_path = _norm_doc_path(raw)

        abs_p = (DOCUMENTS_DIR / Path(*doc_path.split("/"))).resolve()
        try:
            abs_p.relative_to(DOCUMENTS_DIR.resolve())
        except Exception:
            raise HTTPException(400, "file_path must be under documents root")

    if cat not in CATEGORIES:
        raise HTTPException(400, f"Invalid category. Must be one of: {CATEGORIES}")
    if category and category != cat:
        raise HTTPException(400, "category does not match file_path")
    if not doc_path.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")
    if not abs_p.exists():
        raise HTTPException(404, f"PDF not found: {doc_path}")

    return doc_path, cat, abs_p


def _find_latest_task_for_doc(module: str, doc_path: str) -> Optional[tuple[str, Dict[str, Any]]]:
    """Best-effort: find most recently created task for a doc (by created_at string)."""
    doc_path = _norm_doc_path(doc_path)
    best: Optional[tuple[str, Dict[str, Any]]] = None
    for tid, t in _tasks.items():
        if t.get("module") != module:
            continue
        if _norm_doc_path(str(t.get("doc_path") or "")) != doc_path:
            continue
        if best is None or str(t.get("created_at") or "") > str(best[1].get("created_at") or ""):
            best = (tid, t)
    return best


def _find_latest_task_for_module(module: str) -> Optional[tuple[str, Dict[str, Any]]]:
    """Best-effort: find most recently created task for a module."""
    best: Optional[tuple[str, Dict[str, Any]]] = None
    for tid, t in _tasks.items():
        if t.get("module") != module:
            continue
        if best is None or str(t.get("created_at") or "") > str(best[1].get("created_at") or ""):
            best = (tid, t)
    return best


def _status_value(st: Any) -> str:
    if isinstance(st, TaskStatus):
        return st.value
    return str(st or "").lower()


def _is_transient_network_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    try:
        import requests

        if isinstance(exc, requests.exceptions.RequestException):
            return True
    except Exception:
        pass
    msg = str(exc or "").lower()
    markers = (
        "timeout",
        "timed out",
        "temporarily unavailable",
        "connection refused",
        "connection reset",
        "connection aborted",
        "network is unreachable",
        "name or service not known",
        "failed to establish a new connection",
        "max retries exceeded",
        "proxyerror",
        "proxy error",
        "serverselectiontimeout",
        "connection closed",
        "failed to connect",
        "cannot connect",
    )
    return any(m in msg for m in markers)


def _is_duplicate_document_error(exc: Exception) -> bool:
    msg = str(exc or "")
    msg_lower = msg.lower()
    return "e11000 duplicate key error" in msg_lower and "document_id_unique" in msg_lower


def _describe_task_error(status: str, error: Optional[str]) -> Optional[str]:
    msg = str(error or "").strip()
    if not msg:
        return None

    msg_lower = msg.lower()
    is_waiting_network = str(status or "").lower() == "waiting_network"

    if (
        "27017" in msg_lower
        or "mongodb" in msg_lower
        or "serverselectiontimeout" in msg_lower
        or "topologydescription" in msg_lower
    ):
        if "authentication failed" in msg_lower or "auth" in msg_lower:
            return "MongoDB 认证失败，请检查 MONGODB_URI 的用户名、密码和 authSource。"
        if "connection refused" in msg_lower or "winerror 10061" in msg_lower:
            return "MongoDB 未连接，请确认 Docker 已启动且 MongoDB 容器正在运行。"
        if is_waiting_network:
            return "MongoDB 暂时不可用，系统会自动重试。"

    if "19530" in msg_lower or "19532" in msg_lower or "29532" in msg_lower or "milvus" in msg_lower:
        if (
            "connection refused" in msg_lower
            or "failed connecting to server" in msg_lower
            or "fail connecting to server" in msg_lower
        ):
            return "Milvus 未连接，请确认 Docker 已启动且 Milvus 服务正在运行。"
        if is_waiting_network:
            return "Milvus 暂时不可用，系统会自动重试。"

    if is_waiting_network:
        return "依赖服务暂时不可用，系统会自动重试。"

    return None


def _retry_delay_seconds(retry_count: int) -> int:
    attempt = max(1, int(retry_count))
    base = min(VECTOR_RETRY_MAX_DELAY_SEC, 2 ** min(8, attempt - 1))
    jitter = random.uniform(0, max(0.1, base * 0.2))
    return int(max(1, min(VECTOR_RETRY_MAX_DELAY_SEC, round(base + jitter))))


def _append_vector_failure_log(task_id: str, doc_path: str, error: str, extra: Optional[Dict[str, Any]] = None) -> None:
    try:
        VECTOR_FAILURE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "time": datetime.now(timezone.utc).isoformat(),
            "task_id": task_id,
            "doc_path": doc_path,
            "error": error,
            "extra": extra or {},
        }
        with VECTOR_FAILURE_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _rollback_vector_document(doc_identifiers: List[str]) -> tuple[bool, str]:
    doc_ids = [str(x).strip() for x in (doc_identifiers or []) if str(x).strip()]
    if not doc_ids:
        return True, "no_document_identifier"
    try:
        from pymongo import MongoClient

        client = MongoClient(os.getenv("MONGODB_URI"), serverSelectionTimeoutMS=3000)
        db = client[os.getenv("MONGODB_DATABASE", "mediarch")]
        documents = db[os.getenv("MONGODB_DOCUMENT_COLLECTION", "documents")]
        chunks = db[os.getenv("MONGODB_CHUNK_COLLECTION", "mediarch_chunks")]
    except Exception as e:
        return False, f"mongodb_connect_failed: {e}"

    removed_docs = 0
    removed_chunks = 0
    removed_vectors = 0
    try:
        rows = list(
            documents.find(
                {"document_id": {"$in": doc_ids}},
                {"_id": 1},
            )
        )
        mongo_doc_ids = [str(r.get("_id")) for r in rows if r.get("_id") is not None]
        if mongo_doc_ids:
            try:
                from backend.databases.ingestion.indexing.milvus_writer import MilvusWriter

                mw = MilvusWriter(
                    host=os.getenv("MILVUS_HOST", "localhost"),
                    port=os.getenv("MILVUS_PORT", "19530"),
                )
                for mid in mongo_doc_ids:
                    try:
                        removed_vectors += int(mw.delete_by_doc_id(mid) or 0)
                    except Exception:
                        # Ignore single-id failures; Mongo cleanup still proceeds.
                        pass
            except Exception:
                pass
            try:
                chunk_del = chunks.delete_many({"doc_id": {"$in": mongo_doc_ids}})
                removed_chunks += int(getattr(chunk_del, "deleted_count", 0) or 0)
            except Exception:
                pass
            try:
                from bson import ObjectId

                oid_list = [ObjectId(mid) for mid in mongo_doc_ids]
                chunk_del2 = chunks.delete_many({"doc_id": {"$in": oid_list}})
                removed_chunks += int(getattr(chunk_del2, "deleted_count", 0) or 0)
            except Exception:
                pass
            try:
                doc_del = documents.delete_many({"document_id": {"$in": doc_ids}})
                removed_docs += int(getattr(doc_del, "deleted_count", 0) or 0)
            except Exception:
                pass
        return True, f"docs={removed_docs}, chunks={removed_chunks}, vectors={removed_vectors}"
    except Exception as e:
        return False, str(e)
    finally:
        try:
            client.close()
        except Exception:
            pass


def _get_reranker():
    """Lazy-init lightweight reranker for /vector/rerank endpoint."""
    global _reranker_instance
    with _reranker_lock:
        if _reranker_instance is None:
            from data_process.vector.reranker import BgeReranker
            _reranker_instance = BgeReranker()
    return _reranker_instance


def _load_vectorized_document_index() -> Dict[str, Dict[str, Any]]:
    """从 MongoDB 读取已向量化文档索引（key=document_id=file_path）。"""
    uri = os.getenv("MONGODB_URI")
    if not uri:
        return {}
    try:
        from pymongo import MongoClient
        from bson import ObjectId

        client = MongoClient(uri, serverSelectionTimeoutMS=3000)
        db = client[os.getenv("MONGODB_DATABASE", "mediarch")]
        coll = db[os.getenv("MONGODB_DOCUMENT_COLLECTION", "documents")]
        chunk_coll = db[os.getenv("MONGODB_CHUNK_COLLECTION", "mediarch_chunks")]

        # 用 upload_time 倒序选择“最新版本”（避免重复写入导致的多条记录）
        rows = list(
            coll.find(
                {"document_id": {"$exists": True, "$ne": ""}},
                {"document_id": 1, "statistics": 1, "upload_time": 1},
            ).sort("upload_time", -1)
        )
    except Exception:
        return {}

    # 先按 document_id 去重（保留最新）
    index: Dict[str, Dict[str, Any]] = {}
    selected_doc_oids: Dict[str, ObjectId] = {}
    selected_doc_oid_strs: Dict[str, str] = {}

    version_counter: Dict[str, int] = {}
    for row in rows:
        key = str(row.get("document_id", "")).strip()
        if not key:
            continue
        version_counter[key] = int(version_counter.get(key, 0) + 1)
        if key in index:
            continue
        doc_oid = row.get("_id")
        if not isinstance(doc_oid, ObjectId):
            continue
        index[key] = {
            "doc_id": str(doc_oid),
            "statistics": row.get("statistics") or {},
            "version": 1,
        }
        selected_doc_oids[str(doc_oid)] = doc_oid
        selected_doc_oid_strs[str(doc_oid)] = str(doc_oid)

    if not index:
        try:
            client.close()
        except Exception:
            pass
        return index

    # 若统计缺失（或为0），尽力从 chunks 集合聚合补齐（兼容 doc_id: ObjectId / str(ObjectId) 两种写法）
    try:
        oid_list = list(selected_doc_oids.values())
        oid_str_list = list(selected_doc_oid_strs.values())

        def _agg_counts(match_query: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
            pipeline = [
                {"$match": match_query},
                {"$group": {"_id": {"doc_id": "$doc_id", "content_type": "$content_type"}, "count": {"$sum": 1}}},
            ]
            out: Dict[str, Dict[str, int]] = {}
            for r in chunk_coll.aggregate(pipeline):
                _id = r.get("_id") or {}
                doc_id_val = _id.get("doc_id")
                ct = str(_id.get("content_type") or "text")
                c = int(r.get("count") or 0)
                doc_key = str(doc_id_val)
                if not doc_key:
                    continue
                out.setdefault(doc_key, {}).setdefault(ct, 0)
                out[doc_key][ct] += c
            return out

        counts_obj = _agg_counts({"doc_id": {"$in": oid_list}})
        counts_str = _agg_counts({"doc_id": {"$in": oid_str_list}})

        def _merge_stats(doc_id: str) -> Dict[str, int]:
            merged: Dict[str, int] = {}
            for src in (counts_obj.get(doc_id) or {}, counts_str.get(doc_id) or {}):
                for k, v in src.items():
                    merged[k] = int(merged.get(k, 0) + int(v))
            total = sum(merged.values())
            return {
                "total_chunks": total,
                "text_chunks": int(merged.get("text", 0)),
                "image_chunks": int(merged.get("image", 0)),
                "table_chunks": int(merged.get("table", 0)),
            }

        for doc_path, info in index.items():
            stats = info.get("statistics") or {}
            if int(stats.get("total_chunks") or 0) > 0:
                continue
            doc_id = str(info.get("doc_id") or "").strip()
            if not doc_id:
                continue
            info["statistics"] = {**stats, **_merge_stats(doc_id)}

        for doc_path, info in index.items():
            info["version"] = int(version_counter.get(doc_path, 1))

    except Exception:
        pass
    finally:
        try:
            client.close()
        except Exception:
            pass

    return index


def _is_ocr_completed(doc_path: str, category: str = "") -> bool:
    try:
        _doc_path, cat, abs_pdf = _resolve_pdf_under_documents(doc_path, category)
    except Exception:
        return False
    ocr_dir = DOCUMENTS_OCR_DIR / cat / abs_pdf.stem
    return ocr_dir.exists() and bool(list(ocr_dir.rglob("*.md")))


def _page_count_from_detail(detail: List[Dict[str, Any]]) -> int:
    page_ids: set[int] = set()
    for item in detail:
        if not isinstance(item, dict):
            continue
        page_value = item.get("page_id", item.get("page_idx"))
        try:
            page_int = int(page_value)
        except Exception:
            continue
        if "page_idx" in item and "page_id" not in item:
            page_int += 1
        if page_int > 0:
            page_ids.add(page_int)
    return max(page_ids) if page_ids else 0


def _normalize_mineru_detail_items(detail_data: Any) -> List[Dict[str, Any]]:
    if not isinstance(detail_data, list):
        return []

    raw_mineru_signature = any(
        isinstance(item, dict)
        and (
            "page_idx" in item
            or "bbox" in item
            or str(item.get("type") or "").lower() in {"text", "image", "table", "discarded"}
        )
        for item in detail_data
    )
    if not raw_mineru_signature:
        return [item for item in detail_data if isinstance(item, dict)]

    normalized: List[Dict[str, Any]] = []
    paragraph_id = 0

    for item in detail_data:
        if not isinstance(item, dict):
            continue

        item_type = str(item.get("type") or "").lower()
        page_id = int(item.get("page_idx", 0) or 0) + 1
        bbox = item.get("bbox") if isinstance(item.get("bbox"), list) else []

        if item_type == "text":
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            paragraph_id += 1
            text_level = item.get("text_level")
            outline_level = 0 if isinstance(text_level, int) and text_level >= 1 else -1
            normalized.append({
                "outline_level": outline_level,
                "text": text,
                "page_id": page_id,
                "paragraph_id": paragraph_id,
                "type": "paragraph",
                "sub_type": None,
                "position": bbox,
            })
            continue

        if item_type == "image":
            image_path = (
                item.get("image_url")
                or item.get("image_path")
                or item.get("img_path")
                or item.get("path")
            )
            caption = item.get("caption")
            if not caption:
                image_caption = item.get("image_caption")
                if isinstance(image_caption, list):
                    caption = " ".join(str(part).strip() for part in image_caption if str(part).strip())
                elif image_caption:
                    caption = str(image_caption)

            normalized.append({
                "outline_level": -1,
                "text": "",
                "page_id": page_id,
                "paragraph_id": None,
                "type": "image",
                "sub_type": None,
                "position": bbox,
                "image_path": str(image_path) if image_path else None,
                "caption": str(caption).strip() if caption else None,
            })
            continue

        if item_type == "table":
            table_html = str(item.get("table_html") or item.get("table_body") or "").strip()
            table_caption = item.get("table_caption")
            if isinstance(table_caption, list):
                table_caption_list = [str(part).strip() for part in table_caption if str(part).strip()]
            elif table_caption:
                table_caption_list = [str(table_caption).strip()]
            else:
                table_caption_list = []

            normalized.append({
                "outline_level": -1,
                "text": "",
                "page_id": page_id,
                "paragraph_id": None,
                "type": "table",
                "sub_type": None,
                "position": bbox,
                "table_html": table_html,
                "table_caption": table_caption_list,
            })
            continue

        if item_type in {"paragraph", "table", "image"} or "outline_level" in item:
            normalized.append(item)

    return normalized


def _load_ocr_result_from_artifacts(ocr_dir: Path) -> Dict[str, Any]:
    """从 OCR 产物目录还原向量化所需的 OCR JSON。"""
    md_text = ""
    detail: list = []
    total_pages = 0
    success_pages = 0

    def _mtime_ns(path: Path) -> int:
        try:
            return int(path.stat().st_mtime_ns)
        except Exception:
            return 0

    def _prefer_rank(path: Path, preferred_name: str) -> tuple[int, int]:
        n = path.name.lower()
        parts = [str(x).lower() for x in path.parts]
        rank = 2
        if n == preferred_name:
            rank = 0
        elif "full" in parts:
            rank = 1
        return (rank, -_mtime_ns(path))

    md_files = sorted(ocr_dir.rglob("*.md"), key=lambda p: _prefer_rank(p, "full.md"))
    if md_files:
        try:
            md_text = md_files[0].read_text(encoding="utf-8", errors="ignore")
        except Exception:
            md_text = ""

    json_file: Optional[Path] = None
    for pat in ("*_content_list.json", "*_middle.json", "*.json"):
        found = sorted(ocr_dir.rglob(pat), key=lambda p: _prefer_rank(p, "full_content_list.json"))
        if found:
            json_file = found[0]
            break

    if json_file:
        try:
            data = json.loads(json_file.read_text(encoding="utf-8", errors="ignore"))
            if isinstance(data, list):
                detail = _normalize_mineru_detail_items(data)
                total_pages = _page_count_from_detail(detail)
                success_pages = total_pages
            elif isinstance(data, dict):
                pages = data.get("pages")
                if isinstance(pages, list):
                    total_pages = len(pages)
                    success_pages = total_pages
                raw_detail = data.get("detail") if isinstance(data.get("detail"), list) else []
                detail = _normalize_mineru_detail_items(raw_detail)
                if not total_pages:
                    total_pages = _page_count_from_detail(detail)
                    success_pages = total_pages
        except Exception:
            pass

    return {
        "code": 200,
        "message": "ok",
        "result": {
            "markdown": md_text,
            "detail": detail,
            "total_page_number": int(total_pages),
            "success_count": int(success_pages),
        },
    }


def _get_pdf_page_count(pdf_path: Path) -> int:
    try:
        from pypdf import PdfReader

        return int(len(PdfReader(str(pdf_path)).pages))
    except Exception:
        pass
    try:
        from PyPDF2 import PdfReader

        return int(len(PdfReader(str(pdf_path)).pages))
    except Exception:
        pass
    return -1


def _pick_ocr_artifacts_dir(ocr_dir: Path) -> Optional[Path]:
    if not ocr_dir.exists():
        return None
    candidates: List[Path] = []
    full_dir = ocr_dir / "full"
    if full_dir.exists() and full_dir.is_dir() and list(full_dir.rglob("*.md")):
        candidates.append(full_dir)
    if list(ocr_dir.rglob("*.md")):
        candidates.append(ocr_dir)
    if not candidates:
        return None
    try:
        return sorted(
            candidates,
            key=lambda p: (
                0 if p.name.lower() == "full" else 1,
                -int(p.stat().st_mtime_ns),
            ),
        )[0]
    except Exception:
        return candidates[0]


def _evaluate_ocr_readiness(pdf_path: Path, ocr_root_dir: Path) -> Dict[str, Any]:
    artifact_dir = _pick_ocr_artifacts_dir(ocr_root_dir)
    if artifact_dir is None:
        return {
            "ready": False,
            "reason": "ocr_artifacts_missing",
            "artifacts_dir": None,
            "payload": {"result": {}},
            "pdf_pages": -1,
        }

    payload = _load_ocr_result_from_artifacts_cached(artifact_dir)
    result = payload.get("result", {}) if isinstance(payload, dict) else {}
    success_pages = int(result.get("success_count") or 0)
    total_pages = int(result.get("total_page_number") or 0)
    pdf_pages = _get_pdf_page_count(pdf_path)

    if success_pages <= 0 and total_pages <= 0:
        return {
            "ready": False,
            "reason": "ocr_result_empty",
            "artifacts_dir": artifact_dir,
            "payload": payload,
            "pdf_pages": pdf_pages,
        }
    if pdf_pages > 0 and max(success_pages, total_pages) < pdf_pages:
        return {
            "ready": False,
            "reason": "ocr_partial",
            "artifacts_dir": artifact_dir,
            "payload": payload,
            "pdf_pages": pdf_pages,
        }
    return {
        "ready": True,
        "reason": "ok",
        "artifacts_dir": artifact_dir,
        "payload": payload,
        "pdf_pages": pdf_pages,
    }


def _load_ocr_result_from_artifacts_cached(ocr_dir: Path) -> Dict[str, Any]:
    """带轻量缓存的 OCR 产物读取（用于 /vector/list 降低重复 IO）。"""
    if OCR_ARTIFACT_CACHE_SIZE <= 0:
        return _load_ocr_result_from_artifacts(ocr_dir)

    try:
        cache_key = str(ocr_dir.resolve())
    except Exception:
        cache_key = str(ocr_dir)

    try:
        mtime_ns = int(ocr_dir.stat().st_mtime_ns)
    except Exception:
        mtime_ns = -1

    with _ocr_artifacts_cache_lock:
        hit = _ocr_artifacts_cache.get(cache_key)
        if hit and int(hit.get("mtime_ns", -2)) == mtime_ns:
            _ocr_artifacts_cache.move_to_end(cache_key)
            return hit.get("payload") or {"code": 200, "message": "ok", "result": {}}

    payload = _load_ocr_result_from_artifacts(ocr_dir)

    with _ocr_artifacts_cache_lock:
        _ocr_artifacts_cache[cache_key] = {"mtime_ns": mtime_ns, "payload": payload}
        _ocr_artifacts_cache.move_to_end(cache_key)
        while len(_ocr_artifacts_cache) > OCR_ARTIFACT_CACHE_SIZE:
            _ocr_artifacts_cache.popitem(last=False)

    return payload


def _task_jsonable(task: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(task)
    st = out.get("status")
    if isinstance(st, TaskStatus):
        out["status"] = st.value
    return out


def _load_tasks() -> None:
    """Best-effort load persisted task state from disk."""
    if not TASKS_STORE_FILE.exists():
        return
    try:
        data = json.loads(TASKS_STORE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        with _tasks_lock:
            _tasks.clear()
            for tid, t in data.items():
                if not isinstance(t, dict):
                    continue
                st = t.get("status")
                if isinstance(st, str):
                    try:
                        t["status"] = TaskStatus(st)
                    except Exception:
                        # Keep as-is for unknown values.
                        pass
                st_val = _status_value(t.get("status"))
                module = str(t.get("module") or "")
                # Resumable vector tasks survive restart and continue with network-wait state.
                if st_val == "running":
                    if module == "vector" and isinstance(t.get("resume_payload"), dict):
                        t["status"] = TaskStatus.WAITING_NETWORK
                        if not t.get("error"):
                            t["error"] = "Task interrupted by service restart; will resume automatically"
                    elif _kg_task_is_resumable(t):
                        t["status"] = TaskStatus.WAITING_NETWORK
                        if not t.get("error"):
                            t["error"] = "Task interrupted by service restart; will resume automatically"
                    else:
                        t["status"] = TaskStatus.FAILED
                        if not t.get("error"):
                            t["error"] = "Task interrupted by service restart"
                _tasks[str(tid)] = t
    except Exception:
        return


def _save_tasks() -> None:
    """Best-effort persist task state to disk (atomic write)."""
    try:
        TASKS_STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _tasks_lock:
            data = {tid: _task_jsonable(t) for tid, t in _tasks.items()}
        tmp = TASKS_STORE_FILE.with_suffix(TASKS_STORE_FILE.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(TASKS_STORE_FILE)
    except Exception:
        return


def _task_update(task_id: str, **fields: Any) -> None:
    with _tasks_lock:
        if task_id not in _tasks:
            return
        _tasks[task_id].update(fields)
    _save_tasks()


def _kg_task_is_resumable(task: Dict[str, Any]) -> bool:
    return (
        str(task.get("module") or "") == "kg"
        and isinstance(task.get("request_payload"), dict)
    )


def _new_task(module: str) -> str:
    task_id = str(uuid.uuid4())[:12]
    with _tasks_lock:
        _tasks[task_id] = {
            "status": TaskStatus.PENDING,
            "module": module,
            "progress": None,
            "result": None,
            "error": None,
            "created_at": datetime.utcnow().isoformat(),
        }
    _save_tasks()
    return task_id


def _create_neo4j_driver():
    from neo4j import GraphDatabase

    return GraphDatabase.driver(
        os.getenv("NEO4J_URI"),
        auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD")),
    )


def _create_mongo_client():
    from pymongo import MongoClient

    return MongoClient(os.getenv("MONGODB_URI"))


def _create_kg_module(strategy: str = "B1", custom_config: Optional[Dict[str, Any]] = None):
    from data_process.kg.kg_module import KgModule

    if strategy == "custom" and custom_config:
        return KgModule(strategy="custom", custom_config=custom_config)
    return KgModule(strategy=strategy)


def _load_chunks_from_builder_db(module: Any, doc_ids: Optional[list] = None) -> list:
    from bson import ObjectId

    builder = getattr(module, "kg_builder", None)
    db = getattr(builder, "db", None)
    if db is None:
        raise RuntimeError("KG builder database is not available")

    coll = db.get_collection(os.getenv("MONGODB_CHUNK_COLLECTION", "mediarch_chunks"))
    query: Dict[str, Any] = {"content_type": {"$in": ["text", "table"]}}
    if doc_ids:
        normalized_doc_ids: List[Any] = []
        for doc_id in doc_ids:
            try:
                normalized_doc_ids.append(ObjectId(doc_id))
            except Exception:
                normalized_doc_ids.append(doc_id)
        query["doc_id"] = {"$in": normalized_doc_ids}

    chunks = list(coll.find(query, {
        "chunk_id": 1, "content": 1, "content_type": 1,
        "doc_id": 1, "section": 1, "source_document": 1,
    }))

    for c in chunks:
        c["_id"] = str(c["_id"])
        if "doc_id" in c:
            c["doc_id"] = str(c["doc_id"])
    return chunks


def _kg_resume_payload_is_complete(payload: Dict[str, Any], strategy: str, build_signature: str) -> bool:
    if str(payload.get("kind") or "") != "kg_build":
        return False
    if str(payload.get("strategy") or "") != str(strategy):
        return False
    if str(payload.get("build_signature") or "") != str(build_signature):
        return False
    if str(payload.get("resume_from_stage") or "") not in {"triplet_optimization", "cross_document_fusion"}:
        return False
    if not isinstance(payload.get("ea_pairs"), list) or not payload.get("ea_pairs"):
        return False
    if not isinstance(payload.get("triplets"), list) or not payload.get("triplets"):
        return False
    return True


def _find_latest_resumable_kg_payload(
    tasks: Dict[str, Dict[str, Any]],
    strategy: str,
    build_signature: str,
) -> Optional[Dict[str, Any]]:
    best_payload: Optional[Dict[str, Any]] = None
    best_created_at = ""
    for task in tasks.values():
        if task.get("module") != "kg":
            continue
        if _status_value(task.get("status")) != "failed":
            continue
        payload = task.get("resume_payload")
        if not isinstance(payload, dict):
            continue
        if not _kg_resume_payload_is_complete(payload, strategy, build_signature):
            continue
        created_at = str(task.get("created_at") or "")
        if created_at >= best_created_at:
            best_created_at = created_at
            best_payload = dict(payload)
    return best_payload


KG_PROGRESS_STAGE_META: Dict[str, Dict[str, Any]] = {
    "ea_recognition": {
        "label": "E-A 识别",
        "weight": 0.32,
        "mode": "chunk",
        "default_step": "Chunk 处理进度",
    },
    "relation_extraction": {
        "label": "关系抽取",
        "weight": 0.32,
        "mode": "chunk",
        "default_step": "Chunk 处理进度",
    },
    "triplet_optimization": {
        "label": "三元组优化",
        "weight": 0.16,
        "mode": "step",
        "fixed_total": 3,
        "default_step": "优化步骤推进",
    },
    "cross_document_fusion": {
        "label": "跨文档融合",
        "weight": 0.20,
        "mode": "step",
        "fixed_total": 3,
        "default_step": "融合步骤推进",
    },
}

KG_PROGRESS_STEP_LABELS: Dict[str, str] = {
    "ea_recognition": "Chunk 处理进度",
    "relation_extraction": "Chunk 处理进度",
    "optimization_start": "开始优化",
    "name_standardization_done": "实体名称标准化",
    "relation_normalization_done": "关系归一化",
    "validation_done": "验证与去重",
    "fusion_start": "开始融合",
    "entity_dedup_done": "实体去重",
    "latent_recognition_done": "潜在关系识别",
    "neo4j_write_progress": "关系判断进度",
    "neo4j_write_done": "写入 Neo4j",
}

KG_STAGE4_SUBSTAGE_LABELS: Dict[str, str] = {
    "fusion_start": "开始融合",
    "entity_dedup_done": "实体去重",
    "latent_recognition": "潜在关系识别",
    "latent_recognition_done": "潜在关系识别完成",
    "neo4j_write": "写入 Neo4j",
}

KG_PROGRESS_STAGE_ORDER = list(KG_PROGRESS_STAGE_META.keys())


def _kg_progress_display_position(stage_name: str, current: int, total: int) -> tuple[int, int]:
    current_int = max(0, int(current or 0))
    total_int = max(0, int(total or 0))
    stage_meta = KG_PROGRESS_STAGE_META.get(str(stage_name))
    if not stage_meta:
        return current_int, total_int
    if stage_meta.get("mode") == "chunk" and total_int > 0:
        return min(total_int, current_int + 1), total_int
    return current_int, total_int


def _kg_stage_total_units(stage_name: str, total_chunks: Optional[int]) -> Optional[int]:
    stage_meta = KG_PROGRESS_STAGE_META.get(str(stage_name))
    if not stage_meta:
        return None

    if stage_meta.get("mode") == "chunk":
        chunk_total = max(0, int(total_chunks or 0))
        return chunk_total if chunk_total > 0 else None

    fixed_total = max(0, int(stage_meta.get("fixed_total") or 0))
    return fixed_total if fixed_total > 0 else None


def _kg_history_seconds_per_chunk(strategy: Optional[str]) -> tuple[Optional[float], int]:
    if not strategy:
        return None, 0

    per_chunk_values: List[float] = []
    with _kg_history_lock:
        for build in _kg_build_history.values():
            if str(build.get("strategy")) != str(strategy):
                continue
            build_time = float(build.get("build_time_seconds") or 0)
            chunk_count = int(build.get("chunk_count") or 0)
            if build_time > 0 and chunk_count > 0:
                per_chunk_values.append(build_time / chunk_count)

    if not per_chunk_values:
        return None, 0
    return (sum(per_chunk_values) / len(per_chunk_values), len(per_chunk_values))


def _kg_history_stage_seconds_per_unit(
    strategy: Optional[str], stage_name: str
) -> tuple[Optional[float], int]:
    if not strategy:
        return None, 0

    per_unit_values: List[float] = []
    with _kg_history_lock:
        for build in _kg_build_history.values():
            if str(build.get("strategy")) != str(strategy):
                continue
            stage_timings = build.get("stage_timings")
            if not isinstance(stage_timings, dict):
                continue
            stage_timing = stage_timings.get(str(stage_name))
            if not isinstance(stage_timing, dict):
                continue
            duration_seconds = float(stage_timing.get("duration_seconds") or 0)
            unit_total = int(stage_timing.get("unit_total") or 0)
            if unit_total > 0 and duration_seconds >= 0:
                per_unit_values.append(duration_seconds / unit_total)

    if not per_unit_values:
        return None, 0
    return (sum(per_unit_values) / len(per_unit_values), len(per_unit_values))


def _build_kg_stage_timings_payload(
    stage_durations_seconds: Dict[str, float],
    total_chunks: Optional[int],
    stage_results: Optional[List[Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    payload: Dict[str, Dict[str, Any]] = {}
    stage_result_map = {
        str(getattr(stage_result, "stage", "")): stage_result
        for stage_result in (stage_results or [])
    }

    for stage_key in KG_PROGRESS_STAGE_ORDER:
        duration_seconds = stage_durations_seconds.get(stage_key)
        if duration_seconds is None:
            stage_result = stage_result_map.get(stage_key)
            stats = getattr(stage_result, "stats", None)
            if isinstance(stats, dict) and (
                stats.get("refinement_skipped") or stats.get("fusion_skipped")
            ):
                duration_seconds = 0.0

        unit_total = _kg_stage_total_units(stage_key, total_chunks)
        if duration_seconds is None or unit_total is None:
            continue

        payload[stage_key] = {
            "duration_seconds": round(max(0.0, float(duration_seconds)), 3),
            "unit_total": int(unit_total),
        }

    return payload


def _build_kg_progress_extra(
    stage_name: str,
    step_name: str,
    current: int,
    total: int,
    elapsed_seconds: float,
    strategy: Optional[str] = None,
    total_chunks: Optional[int] = None,
) -> Dict[str, Any]:
    stage_key = str(stage_name or "")
    step_key = str(step_name or stage_key)
    stage_meta = KG_PROGRESS_STAGE_META.get(stage_key)
    display_current, display_total = _kg_progress_display_position(stage_key, current, total)
    relation_judgement_processed = None
    relation_judgement_total = None
    relation_judgement_percent = None

    if display_total > 0:
        stage_fraction = max(0.0, min(1.0, display_current / display_total))
    else:
        stage_fraction = 0.0

    if stage_key == "cross_document_fusion" and step_key == "neo4j_write_progress":
        relation_judgement_processed = max(0, int(current or 0))
        relation_judgement_total = max(0, int(total or 0))
        relation_judgement_percent = int(round(stage_fraction * 100)) if relation_judgement_total > 0 else 0
        stage_fraction = min(1.0, (2.0 / 3.0) + (stage_fraction / 3.0))
    stage_percent_value = (
        relation_judgement_percent
        if relation_judgement_percent is not None
        else int(round(stage_fraction * 100))
    )

    completed_weight = 0.0
    for key in KG_PROGRESS_STAGE_ORDER:
        if key == stage_key:
            break
        completed_weight += float(KG_PROGRESS_STAGE_META[key]["weight"])

    if stage_meta:
        overall_fraction = max(
            0.0,
            min(0.99, completed_weight + float(stage_meta["weight"]) * stage_fraction),
        )
    else:
        overall_fraction = 0.0

    stage_history_sample_count_by_stage: Dict[str, int] = {}
    current_stage_history_avg_seconds_per_unit: Optional[float] = None
    current_stage_history_sample_count = 0
    stage_history_estimated_totals: Dict[str, float] = {}

    for history_stage_key in KG_PROGRESS_STAGE_ORDER:
        avg_seconds_per_unit, sample_count = _kg_history_stage_seconds_per_unit(
            strategy, history_stage_key
        )
        stage_history_sample_count_by_stage[history_stage_key] = sample_count

        if history_stage_key == stage_key:
            current_stage_history_avg_seconds_per_unit = avg_seconds_per_unit
            current_stage_history_sample_count = sample_count

        stage_unit_total = _kg_stage_total_units(history_stage_key, total_chunks)
        if avg_seconds_per_unit is not None and stage_unit_total is not None:
            stage_history_estimated_totals[history_stage_key] = (
                avg_seconds_per_unit * stage_unit_total
            )

    stage_history_estimated_total_seconds: Optional[float] = None
    stage_runtime_estimated_total_seconds: Optional[float] = None
    stage_history_reference_elapsed_seconds: Optional[float] = None
    stage_model_ready = all(
        history_stage_key in stage_history_estimated_totals
        for history_stage_key in KG_PROGRESS_STAGE_ORDER
    )
    stage_history_sample_count = 0

    if stage_model_ready:
        stage_history_estimated_total_seconds = sum(
            stage_history_estimated_totals[history_stage_key]
            for history_stage_key in KG_PROGRESS_STAGE_ORDER
        )
        stage_history_sample_count = min(
            stage_history_sample_count_by_stage.get(history_stage_key, 0)
            for history_stage_key in KG_PROGRESS_STAGE_ORDER
        )

        if stage_key in KG_PROGRESS_STAGE_ORDER:
            reference_elapsed = 0.0
            for history_stage_key in KG_PROGRESS_STAGE_ORDER:
                stage_total_estimate = stage_history_estimated_totals[history_stage_key]
                if history_stage_key == stage_key:
                    reference_elapsed += stage_total_estimate * stage_fraction
                    break
                reference_elapsed += stage_total_estimate

            if reference_elapsed > 0:
                stage_history_reference_elapsed_seconds = reference_elapsed
                stage_runtime_estimated_total_seconds = (
                    stage_history_estimated_total_seconds
                    * max(0.0, float(elapsed_seconds or 0))
                    / reference_elapsed
                )

    history_avg_seconds_per_chunk, history_sample_count = _kg_history_seconds_per_chunk(strategy)
    history_estimated_total_seconds: Optional[float] = None
    if history_avg_seconds_per_chunk is not None and total_chunks and int(total_chunks) > 0:
        history_estimated_total_seconds = history_avg_seconds_per_chunk * int(total_chunks)

    runtime_estimated_total_seconds: Optional[float] = None
    if elapsed_seconds > 0 and overall_fraction > 0:
        runtime_estimated_total_seconds = elapsed_seconds / overall_fraction

    estimate_source = "none"
    estimate_strategy = "overall_model"
    estimated_total_seconds: Optional[float] = None
    if stage_history_estimated_total_seconds is not None:
        estimate_strategy = "stage_model"
        history_estimated_total_seconds = stage_history_estimated_total_seconds
        runtime_estimated_total_seconds = stage_runtime_estimated_total_seconds

        if stage_runtime_estimated_total_seconds is not None:
            runtime_weight = overall_fraction
            history_weight = 1.0 - runtime_weight
            estimated_total_seconds = (
                stage_runtime_estimated_total_seconds * runtime_weight
                + stage_history_estimated_total_seconds * history_weight
            )
            estimate_source = "stage_blended"
        else:
            estimated_total_seconds = stage_history_estimated_total_seconds
            estimate_source = "stage_history"
    elif runtime_estimated_total_seconds is not None and history_estimated_total_seconds is not None:
        runtime_weight = overall_fraction
        history_weight = 1.0 - runtime_weight
        estimated_total_seconds = (
            runtime_estimated_total_seconds * runtime_weight
            + history_estimated_total_seconds * history_weight
        )
        estimate_source = "blended"
    elif runtime_estimated_total_seconds is not None:
        estimated_total_seconds = runtime_estimated_total_seconds
        estimate_source = "runtime"
    elif history_estimated_total_seconds is not None:
        estimated_total_seconds = history_estimated_total_seconds
        estimate_source = "history"

    remaining_seconds: Optional[float] = None
    if estimated_total_seconds is not None:
        remaining_seconds = max(0.0, estimated_total_seconds - max(0.0, float(elapsed_seconds or 0)))

    if stage_meta and stage_meta.get("mode") == "chunk" and display_total > 0:
        step_label = f"Chunk {display_current} / {display_total}"
    else:
        step_label = KG_PROGRESS_STEP_LABELS.get(step_key) or (
            stage_meta.get("default_step") if stage_meta else step_key
        )

    return {
        "progress_kind": "kg_build",
        "strategy": strategy,
        "stage_key": stage_key,
        "stage_label": stage_meta.get("label") if stage_meta else stage_key,
        "step_key": step_key,
        "step_label": step_label,
        "overall_fraction": round(overall_fraction, 4),
        "overall_percent": int(round(overall_fraction * 100)),
        "stage_percent": stage_percent_value,
        "elapsed_seconds": int(round(max(0.0, float(elapsed_seconds or 0)))),
        "estimated_total_seconds": (
            int(round(estimated_total_seconds)) if estimated_total_seconds is not None else None
        ),
        "remaining_seconds": (
            int(round(remaining_seconds)) if remaining_seconds is not None else None
        ),
        "runtime_estimated_total_seconds": (
            int(round(runtime_estimated_total_seconds))
            if runtime_estimated_total_seconds is not None else None
        ),
        "history_estimated_total_seconds": (
            int(round(history_estimated_total_seconds))
            if history_estimated_total_seconds is not None else None
        ),
        "estimate_source": estimate_source,
        "estimate_strategy": estimate_strategy,
        "history_sample_count": (
            stage_history_sample_count if estimate_strategy == "stage_model" else history_sample_count
        ),
        "history_avg_seconds_per_chunk": (
            round(history_avg_seconds_per_chunk, 2)
            if history_avg_seconds_per_chunk is not None else None
        ),
        "stage_history_sample_count": current_stage_history_sample_count,
        "stage_history_avg_seconds_per_unit": (
            round(current_stage_history_avg_seconds_per_unit, 2)
            if current_stage_history_avg_seconds_per_unit is not None else None
        ),
        "stage_history_sample_count_by_stage": stage_history_sample_count_by_stage,
        "stage_history_reference_elapsed_seconds": (
            int(round(stage_history_reference_elapsed_seconds))
            if stage_history_reference_elapsed_seconds is not None else None
        ),
        "current_display": display_current,
        "total_display": display_total,
        "total_chunks": int(total_chunks) if total_chunks is not None else None,
        "relation_judgement_processed": relation_judgement_processed,
        "relation_judgement_total": relation_judgement_total,
        "relation_judgement_percent": relation_judgement_percent,
    }


def _stage_checkpoint_progress_position(
    stage_name: str,
    checkpoint_payload: Dict[str, Any],
) -> tuple[str, int, int]:
    stage_key = str(stage_name or "")
    substage = str((checkpoint_payload or {}).get("substage") or "").strip()

    if stage_key != "cross_document_fusion":
        return substage or stage_key, 0, 0

    if substage == "fusion_start":
        return "fusion_start", 0, 3
    if substage == "entity_dedup_done":
        return "entity_dedup_done", 1, 3
    if substage == "latent_recognition":
        return "latent_recognition", 1, 3
    if substage == "latent_recognition_done":
        return "latent_recognition_done", 2, 3
    if substage == "neo4j_write":
        write_progress = dict((checkpoint_payload or {}).get("write_progress") or {})
        current = max(0, int(write_progress.get("processed_count") or 0))
        total = max(
            0,
            int(write_progress.get("total_triplets") or 0)
            or len(list((checkpoint_payload or {}).get("final_triplets") or [])),
        )
        return "neo4j_write_progress", current, total
    return substage or stage_key, 0, 0


def _build_stage4_checkpoint_extra(checkpoint_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(checkpoint_payload or {})
    substage = str(payload.get("substage") or "").strip()
    merge_map = dict(payload.get("merge_map") or {})
    fused_triplets = list(payload.get("fused_triplets") or [])
    latent_triplets = list(payload.get("latent_triplets") or [])
    final_triplets = list(payload.get("final_triplets") or [])
    latent_progress = dict(payload.get("latent_progress") or {})
    write_progress = dict(payload.get("write_progress") or {})

    extra: Dict[str, Any] = {
        "stage4_substage": substage or None,
        "stage4_substage_label": KG_STAGE4_SUBSTAGE_LABELS.get(substage, substage or None),
        "stage4_entities_merged": len(merge_map),
        "stage4_fused_triplets": len(fused_triplets),
        "stage4_latent_triplets": len(latent_triplets),
        "stage4_final_triplets": len(final_triplets),
        "stage4_latent_rounds": int(payload.get("latent_rounds") or 0),
        "stage4_latent_new_counts": list(payload.get("latent_new_counts") or []),
        "stage4_latent_pairs_total": int(payload.get("latent_candidate_pairs_total") or 0),
        "stage4_latent_round": int(latent_progress.get("current_round") or 0),
        "stage4_latent_next_batch_start": int(latent_progress.get("next_batch_start") or 0),
        "stage4_latent_current_round_new_count": int(
            latent_progress.get("current_round_new_count") or 0
        ),
    }

    if write_progress:
        extra.update({
            "stage4_write_phase": str(write_progress.get("write_phase") or ""),
            "stage4_write_processed": int(write_progress.get("processed_count") or 0),
            "stage4_write_total": int(write_progress.get("total_triplets") or 0),
            "stage4_write_batches_done": int(write_progress.get("batches_done") or 0),
            "stage4_write_batches_total": int(write_progress.get("batches_total") or 0),
            "stage4_review_items_done": int(write_progress.get("review_items_done") or 0),
            "stage4_review_items_total": int(write_progress.get("review_items_total") or 0),
            "stage4_review_batches_done": int(write_progress.get("review_batches_done") or 0),
            "stage4_review_batches_total": int(write_progress.get("review_batches_total") or 0),
            "stage4_accept_count": int(write_progress.get("accept_count") or 0),
            "stage4_reject_count": int(write_progress.get("reject_count") or 0),
            "stage4_skip_count": int(write_progress.get("skip_count") or 0),
        })
    return extra


def _update_task_progress_from_stage_checkpoint(
    task_id: str,
    stage_name: str,
    checkpoint_payload: Dict[str, Any],
    *,
    elapsed_seconds: float,
    strategy: Optional[str],
    total_chunks: Optional[int],
) -> None:
    if str(stage_name) != "cross_document_fusion" or not isinstance(checkpoint_payload, dict):
        return

    step_name, current, total = _stage_checkpoint_progress_position(stage_name, checkpoint_payload)
    extra = _build_kg_progress_extra(
        stage_name=str(stage_name),
        step_name=step_name,
        current=int(current),
        total=int(total),
        elapsed_seconds=elapsed_seconds,
        strategy=strategy,
        total_chunks=total_chunks,
    )
    extra.update(_build_stage4_checkpoint_extra(checkpoint_payload))

    update = ProgressUpdate(
        task_id=task_id,
        module="kg",
        stage=f"{stage_name}:{str((checkpoint_payload or {}).get('substage') or step_name)}",
        current=int(extra.get("current_display") or current),
        total=int(extra.get("total_display") or total),
        message=str(extra.get("stage4_substage_label") or extra.get("step_label") or ""),
        extra=extra,
    )
    _task_update(task_id, progress=update.model_dump())


def _env_flag_enabled(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_auto_kg_strategy(raw_strategy: Optional[str]) -> str:
    strategy = str(raw_strategy or "").strip() or "B3"
    if strategy.upper() == "R2":
        logger.info("Auto KG startup maps retrieval mode R2 to KG strategy B3")
        return "B3"

    from data_process.kg.strategy_presets import get_strategy_config

    return str(get_strategy_config(strategy).get("canonical_id") or strategy)


def _parse_auto_kg_doc_ids(raw_value: Optional[str]) -> List[str]:
    doc_ids: List[str] = []
    for raw_part in str(raw_value or "").replace("\n", ",").split(","):
        part = str(raw_part).strip()
        if part:
            doc_ids.append(part)
    return doc_ids


def _has_active_kg_task() -> bool:
    with _tasks_lock:
        for task in _tasks.values():
            if str(task.get("module") or "") != "kg":
                continue
            if _status_value(task.get("status")) in {"pending", "running", "waiting_network"}:
                return True
    return False


def _maybe_start_auto_kg_build_on_startup() -> Optional[str]:
    if not _env_flag_enabled("DATA_PROCESS_AUTO_START_KG", default=False):
        return None
    if _has_active_kg_task():
        logger.info("Skip auto KG startup because an active KG task already exists")
        return None

    try:
        strategy = _normalize_auto_kg_strategy(os.getenv("DATA_PROCESS_AUTO_START_KG_STRATEGY", "B3"))
    except Exception as exc:
        logger.warning("Invalid auto KG strategy, fallback to B3: %s", exc)
        strategy = "B3"

    payload: Dict[str, Any] = {
        "source": "mongodb",
        "mongo_doc_ids": _parse_auto_kg_doc_ids(os.getenv("DATA_PROCESS_AUTO_START_KG_DOC_IDS")),
        "strategy": strategy,
        "custom_config": None,
        "experiment_label": os.getenv("DATA_PROCESS_AUTO_START_KG_EXPERIMENT_LABEL"),
        "save_to_history": _env_flag_enabled("DATA_PROCESS_AUTO_START_KG_SAVE_HISTORY", default=True),
        "ea_max_rounds": 5,
        "ea_threshold": 3,
        "rel_max_rounds": 4,
        "rel_threshold": 2,
        "enable_fusion": None,
        "explicit_fields": [],
    }

    task_id = _new_task("kg")
    _task_update(task_id, request_payload=payload)
    _start_kg_worker(task_id, payload, loop=None)
    logger.info("Auto KG startup scheduled task %s with strategy %s", task_id, strategy)
    return task_id


def _clear_kg_keep_skeleton() -> Dict[str, Any]:
    """清除 KG 构建结果，但保留骨架节点和骨架关系。"""
    neo4j_database = os.getenv("NEO4J_DATABASE", "neo4j")
    mongo_database = os.getenv("MONGODB_DATABASE", "mediarch")
    chunk_collection_name = os.getenv("MONGODB_CHUNK_COLLECTION", "mediarch_chunks")

    driver = _create_neo4j_driver()
    mongo_client = _create_mongo_client()

    try:
        with driver.session(database=neo4j_database) as session:
            total_nodes_before = int(
                session.run("MATCH (n) RETURN count(n) as count").single()["count"] or 0
            )
            total_relationships_before = int(
                session.run("MATCH ()-[r]->() RETURN count(r) as count").single()["count"] or 0
            )
            preserved_skeleton_nodes = int(
                session.run(
                    """
                    MATCH (n)
                    WHERE n.seed_source IS NOT NULL OR n.is_concept = true
                    RETURN count(n) as count
                    """
                ).single()["count"] or 0
            )
            deletable_nodes = int(
                session.run(
                    """
                    MATCH (n)
                    WHERE n.seed_source IS NULL AND (n.is_concept IS NULL OR n.is_concept = false)
                    RETURN count(n) as count
                    """
                ).single()["count"] or 0
            )

            deleted_nodes = 0
            if deletable_nodes > 0:
                session.run(
                    """
                    MATCH (n)
                    WHERE n.seed_source IS NULL AND (n.is_concept IS NULL OR n.is_concept = false)
                    DETACH DELETE n
                    """
                )
                deleted_nodes = deletable_nodes

            remaining_nodes = int(
                session.run("MATCH (n) RETURN count(n) as count").single()["count"] or 0
            )
            remaining_relationships = int(
                session.run("MATCH ()-[r]->() RETURN count(r) as count").single()["count"] or 0
            )

        db = mongo_client[mongo_database]
        chunks_collection = db.get_collection(chunk_collection_name)
        total_chunks = int(chunks_collection.count_documents({}) or 0)
        processed_chunks_before = int(
            chunks_collection.count_documents({"kg_processed": True}) or 0
        )
        processed_chunks_cleared = 0
        if processed_chunks_before > 0:
            update_result = chunks_collection.update_many(
                {"kg_processed": True},
                {"$unset": {"kg_processed": "", "kg_processed_at": ""}},
            )
            processed_chunks_cleared = int(update_result.modified_count or 0)

        remaining_processed_chunks = int(
            chunks_collection.count_documents({"kg_processed": True}) or 0
        )

        return {
            "message": "KG build data cleared while preserving skeleton graph",
            "neo4j": {
                "total_nodes_before": total_nodes_before,
                "total_relationships_before": total_relationships_before,
                "preserved_skeleton_nodes": preserved_skeleton_nodes,
                "deleted_nodes": deleted_nodes,
                "deleted_relationships": max(0, total_relationships_before - remaining_relationships),
                "remaining_nodes": remaining_nodes,
                "remaining_relationships": remaining_relationships,
            },
            "mongodb": {
                "total_chunks": total_chunks,
                "processed_chunks_before": processed_chunks_before,
                "processed_chunks_cleared": processed_chunks_cleared,
                "remaining_processed_chunks": remaining_processed_chunks,
            },
        }
    finally:
        try:
            driver.close()
        except Exception:
            pass
        try:
            mongo_client.close()
        except Exception:
            pass


async def _send_progress(task_id: str, update: ProgressUpdate):
    _task_update(task_id, progress=update.model_dump())
    ws = _ws_connections.get(task_id)
    if ws:
        try:
            await ws.send_json(update.model_dump())
        except Exception:
            pass


def _sync_progress_factory(task_id: str, module: str, loop: Optional[asyncio.AbstractEventLoop] = None):
    """创建同步回调，桥接到异步 WebSocket 推送。"""
    def callback(stage, current, total, *args):
        msg = ""
        extra: Dict[str, Any] = {}
        for arg in args:
            if isinstance(arg, dict):
                extra.update(arg)
            elif msg == "" and arg is not None:
                msg = str(arg)
        update = ProgressUpdate(
            task_id=task_id, module=module, stage=str(stage),
            current=int(current), total=int(total), message=msg, extra=extra,
        )
        _task_update(task_id, progress=update.model_dump())
        # 终端进度输出
        pct = int(round((int(current) / int(total)) * 100)) if int(total) > 0 else 0
        print(f"[{module.upper()}][{task_id}] {stage} {current}/{total} ({pct}%){' - ' + msg if msg else ''}", flush=True)
        if loop is None:
            return
        send_coro = _send_progress(task_id, update)
        try:
            asyncio.run_coroutine_threadsafe(send_coro, loop)
        except Exception:
            send_coro.close()
    return callback


def _vector_result_payload(result: Any) -> Dict[str, Any]:
    return {
        "doc_id": result.doc_id,
        "total_chunks": result.total_chunks,
        "text_chunks": result.text_chunks,
        "image_chunks": result.image_chunks,
        "table_chunks": result.table_chunks,
        "embeddings_written": result.embeddings_written,
        "chunks_inserted": result.chunks_inserted,
        "duration_s": result.duration_s,
    }


def _collect_doc_identifiers_from_payload(payload: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    kind = str(payload.get("kind") or "")
    if kind == "from_ocr":
        doc_path = _norm_doc_path(str(payload.get("doc_path") or ""))
        if doc_path:
            out.append(doc_path)
        category = str(payload.get("category") or "")
        if doc_path:
            try:
                _doc_path, _cat, abs_pdf = _resolve_pdf_under_documents(doc_path, category)
                out.append(str(abs_pdf))
            except Exception:
                pass
    elif kind == "direct":
        doc_metadata = payload.get("doc_metadata") or {}
        file_path = str(doc_metadata.get("file_path") or "").strip()
        if file_path:
            out.append(_norm_doc_path(file_path))
            out.append(file_path)
    return list(dict.fromkeys(out))


def _find_existing_vector_info(doc_identifiers: List[str]) -> Optional[Dict[str, Any]]:
    idx = _load_vectorized_document_index()
    if not idx:
        return None
    candidates: List[str] = []
    for ident in doc_identifiers or []:
        raw = str(ident or "").strip()
        if not raw:
            continue
        candidates.append(raw)
        candidates.append(_norm_doc_path(raw))
    for key in dict.fromkeys(candidates):
        info = idx.get(key)
        if info:
            return info
    return None


def _build_vector_doc_metadata_from_ocr(
    abs_pdf: Path,
    category: str,
    title: str,
    artifact_dir: Path,
) -> Dict[str, str]:
    category_value = str(category or "").strip()
    if not category_value:
        category_value = "未分类"
    return {
        "title": title or abs_pdf.stem,
        "category": category_value,
        "type": category_value,
        "source_category": category_value,
        "file_path": str(abs_pdf),
        "source_document": abs_pdf.name,
        "artifacts_dir": str(artifact_dir.resolve()),
    }


def _run_vector_once(task_id: str, payload: Dict[str, Any], loop: Optional[asyncio.AbstractEventLoop]) -> Any:
    from data_process.vector.vector_module import VectorModule

    kind = str(payload.get("kind") or "")
    module = VectorModule()
    cb = _sync_progress_factory(task_id, "vector", loop)
    if kind == "from_ocr":
        doc_path = _norm_doc_path(str(payload.get("doc_path") or ""))
        category = str(payload.get("category") or "")
        title = str(payload.get("title") or "")
        _doc_path, cat, abs_pdf = _resolve_pdf_under_documents(doc_path, category)
        ocr_dir = DOCUMENTS_OCR_DIR / cat / abs_pdf.stem
        readiness = _evaluate_ocr_readiness(abs_pdf, ocr_dir)
        artifact_dir = readiness.get("artifacts_dir")
        if artifact_dir is None:
            raise RuntimeError(f"OCR artifacts not found: {ocr_dir}")
        if not readiness.get("ready"):
            raise RuntimeError("OCR not fully completed for this file yet.")
        ocr_result = _load_ocr_result_from_artifacts(Path(str(artifact_dir)))
        doc_metadata = _build_vector_doc_metadata_from_ocr(
            abs_pdf=abs_pdf,
            category=cat,
            title=title,
            artifact_dir=Path(str(artifact_dir)),
        )
        return module.vectorize_document(
            ocr_result=ocr_result,
            doc_metadata=doc_metadata,
            progress_callback=cb,
        )
    if kind == "direct":
        return module.vectorize_document(
            ocr_result=payload.get("ocr_result") or {},
            doc_metadata=payload.get("doc_metadata") or {},
            progress_callback=cb,
        )
    raise RuntimeError(f"Unknown vector task payload kind: {kind}")


def _vector_task_worker(
    task_id: str, payload: Dict[str, Any], loop: Optional[asyncio.AbstractEventLoop]
) -> None:
    retry_count = 0
    with _tasks_lock:
        current = _tasks.get(task_id) or {}
        retry_count = int(current.get("retry_count") or 0)
    doc_identifiers = _collect_doc_identifiers_from_payload(payload)
    doc_key = doc_identifiers[0] if doc_identifiers else ""
    force_rebuild = bool(payload.get("force"))
    force_cleanup_done = False
    print(f"[VECTOR][{task_id}] 开始处理: {doc_key}", flush=True)
    try:
        while True:
            with _tasks_lock:
                current = _tasks.get(task_id)
                if not current:
                    return
                if _status_value(current.get("status")) in {"completed", "failed"}:
                    return
            _task_update(
                task_id,
                status=TaskStatus.RUNNING,
                next_retry_at=None,
                last_error_type=None,
            )
            try:
                if force_rebuild and not force_cleanup_done:
                    cleanup_ok, cleanup_msg = _rollback_vector_document(doc_identifiers)
                    if not cleanup_ok:
                        raise RuntimeError(f"force cleanup failed: {cleanup_msg}")
                    _task_update(
                        task_id,
                        force_cleanup={"ok": cleanup_ok, "message": cleanup_msg},
                    )
                    force_cleanup_done = True
                result = _run_vector_once(task_id, payload, loop)
                _task_update(
                    task_id,
                    status=TaskStatus.COMPLETED,
                    result=_vector_result_payload(result),
                    error=None,
                    retry_count=retry_count,
                    next_retry_at=None,
                )
                print(f"[VECTOR][{task_id}] 完成: {doc_key} (chunks={result.total_chunks}, embeddings={result.embeddings_written})", flush=True)
                return
            except Exception as e:
                err = str(e)
                if _is_transient_network_error(e):
                    retry_count += 1
                    delay = _retry_delay_seconds(retry_count)
                    next_retry = (
                        datetime.now(timezone.utc) + timedelta(seconds=delay)
                    ).isoformat()
                    print(f"[VECTOR][{task_id}] 网络错误 (第{retry_count}次重试, {delay}s后): {err}", flush=True)
                    _task_update(
                        task_id,
                        status=TaskStatus.WAITING_NETWORK,
                        error=err,
                        retry_count=retry_count,
                        next_retry_at=next_retry,
                        last_error_type="network",
                    )
                    time.sleep(delay)
                    continue
                if _is_duplicate_document_error(e):
                    existing = _find_existing_vector_info(doc_identifiers)
                    if existing:
                        stats = existing.get("statistics") or {}
                        _task_update(
                            task_id,
                            status=TaskStatus.COMPLETED,
                            result={
                                "doc_id": str(existing.get("doc_id") or ""),
                                "total_chunks": int(stats.get("total_chunks") or 0),
                                "text_chunks": int(stats.get("text_chunks") or 0),
                                "image_chunks": int(stats.get("image_chunks") or 0),
                                "table_chunks": int(stats.get("table_chunks") or 0),
                                "embeddings_written": 0,
                                "chunks_inserted": 0,
                                "duration_s": 0.0,
                                "deduplicated": True,
                            },
                            error=None,
                            retry_count=retry_count,
                            next_retry_at=None,
                            last_error_type="duplicate",
                        )
                        print(
                            f"[VECTOR][{task_id}] 已存在，跳过重复入库: {doc_key}",
                            flush=True,
                        )
                        return
                    _task_update(
                        task_id,
                        status=TaskStatus.FAILED,
                        error="Duplicate document_id detected, but existing document record was not found. "
                              "Retry with force=true.",
                        retry_count=retry_count,
                        next_retry_at=None,
                        last_error_type="duplicate",
                        rollback={"ok": True, "message": "skipped due duplicate key"},
                    )
                    _append_vector_failure_log(
                        task_id=task_id,
                        doc_path=doc_key,
                        error=err,
                        extra={"duplicate_handled": True, "rollback_skipped": True},
                    )
                    return
                rollback_ok, rollback_msg = _rollback_vector_document(doc_identifiers)
                print(f"[VECTOR][{task_id}] 失败: {doc_key}\n  错误: {err}\n  回滚: {rollback_msg}", flush=True)
                _task_update(
                    task_id,
                    status=TaskStatus.FAILED,
                    error=err,
                    retry_count=retry_count,
                    next_retry_at=None,
                    last_error_type="fatal",
                    rollback={"ok": rollback_ok, "message": rollback_msg},
                )
                _append_vector_failure_log(
                    task_id=task_id,
                    doc_path=doc_key,
                    error=err,
                    extra={"rollback_ok": rollback_ok, "rollback_message": rollback_msg},
                )
                return
    finally:
        with _vector_worker_lock:
            _vector_workers.pop(task_id, None)


def _start_vector_worker(
    task_id: str, payload: Dict[str, Any], loop: Optional[asyncio.AbstractEventLoop]
) -> None:
    with _vector_worker_lock:
        worker = _vector_workers.get(task_id)
        if worker and worker.is_alive():
            return
        worker = threading.Thread(
            target=_vector_task_worker,
            args=(task_id, payload, loop),
            daemon=True,
            name=f"vector-task-{task_id}",
        )
        _vector_workers[task_id] = worker
        worker.start()


def _kg_request_payload_from_request(req: KgBuildRequest) -> Dict[str, Any]:
    explicit_fields = getattr(req, "model_fields_set", getattr(req, "__fields_set__", set()))
    return {
        "source": getattr(req, "source", "mongodb"),
        "mongo_doc_ids": list(getattr(req, "mongo_doc_ids", None) or []),
        "chunks": getattr(req, "chunks", None),
        "strategy": getattr(req, "strategy", "B1"),
        "custom_config": getattr(req, "custom_config", None),
        "experiment_label": getattr(req, "experiment_label", None),
        "save_to_history": bool(getattr(req, "save_to_history", True)),
        "ea_max_rounds": int(getattr(req, "ea_max_rounds", 5)),
        "ea_threshold": int(getattr(req, "ea_threshold", 3)),
        "rel_max_rounds": int(getattr(req, "rel_max_rounds", 4)),
        "rel_threshold": int(getattr(req, "rel_threshold", 2)),
        "enable_fusion": getattr(req, "enable_fusion", None),
        "explicit_fields": sorted(str(x) for x in explicit_fields),
    }


def _kg_task_worker(
    task_id: str, payload: Dict[str, Any], loop: Optional[asyncio.AbstractEventLoop]
) -> None:
    print(f"[KG][{task_id}] 开始构建知识图谱", flush=True)
    try:
        _task_update(task_id, status=TaskStatus.RUNNING, error=None, request_payload=dict(payload or {}))

        strategy = str(payload.get("strategy") or "B1")
        custom_config = payload.get("custom_config")
        experiment_label = payload.get("experiment_label")
        save_to_history = bool(payload.get("save_to_history", True))

        module = _create_kg_module(strategy=strategy, custom_config=custom_config)

        explicit_fields = set(payload.get("explicit_fields") or [])
        if "ea_max_rounds" in explicit_fields:
            module.EA_MAX_ROUNDS = int(payload.get("ea_max_rounds") or module.EA_MAX_ROUNDS)
        if "ea_threshold" in explicit_fields:
            module.EA_NEW_THRESHOLD = int(payload.get("ea_threshold") or module.EA_NEW_THRESHOLD)
        if "rel_max_rounds" in explicit_fields:
            module.REL_MAX_ROUNDS = int(payload.get("rel_max_rounds") or module.REL_MAX_ROUNDS)
        if "rel_threshold" in explicit_fields:
            module.REL_NEW_THRESHOLD = int(payload.get("rel_threshold") or module.REL_NEW_THRESHOLD)
        if hasattr(module, "_configure_builder_runtime"):
            module._configure_builder_runtime()

        if payload.get("source") == "chunks" and payload.get("chunks"):
            chunks = list(payload.get("chunks") or [])
        else:
            chunks = _load_chunks_from_builder_db(module, payload.get("mongo_doc_ids"))

        total_chunks = len(chunks)
        build_signature = (
            module._build_runtime_signature(chunks)
            if hasattr(module, "_build_runtime_signature")
            else ""
        )

        current_task = _tasks.get(task_id) or {}
        current_resume_payload = current_task.get("resume_payload")
        resume_candidate = (
            dict(current_resume_payload)
            if isinstance(current_resume_payload, dict)
            and str(current_resume_payload.get("build_signature") or "") == str(build_signature)
            else None
        )
        if resume_candidate is None:
            resume_candidate = _find_latest_resumable_kg_payload(
                _tasks,
                strategy=strategy,
                build_signature=build_signature,
            )
        if resume_candidate is None and hasattr(module, "build_resume_artifacts_from_runtime_cache"):
            runtime_resume = module.build_resume_artifacts_from_runtime_cache(chunks)
            if isinstance(runtime_resume, dict):
                runtime_resume.setdefault("kind", "kg_build")
                runtime_resume.setdefault("strategy", strategy)
                runtime_resume.setdefault("build_signature", build_signature)
                resume_candidate = runtime_resume

        base_resume_payload: Dict[str, Any] = {
            "kind": "kg_build",
            "strategy": strategy,
            "build_signature": build_signature,
            "resume_from_stage": "ea_recognition",
            "total_chunks": total_chunks,
            "source": payload.get("source"),
            "mongo_doc_ids": list(payload.get("mongo_doc_ids") or []),
        }
        _task_update(task_id, resume_payload=base_resume_payload)

        cb_raw = _sync_progress_factory(task_id, "kg", loop)

        start_time = datetime.now()
        stage_durations_seconds: Dict[str, float] = {}
        active_stage_name: Optional[str] = None
        active_stage_started_at = start_time

        def kg_cb(stage_name, step, current, total):
            nonlocal active_stage_name, active_stage_started_at
            now = datetime.now()
            normalized_stage_name = str(stage_name)

            if normalized_stage_name != active_stage_name:
                if active_stage_name is not None:
                    stage_durations_seconds[active_stage_name] = max(
                        0.0,
                        (now - active_stage_started_at).total_seconds(),
                    )
                    active_stage_started_at = now
                else:
                    active_stage_started_at = start_time
                active_stage_name = normalized_stage_name

            elapsed_seconds = (datetime.now() - start_time).total_seconds()
            extra = _build_kg_progress_extra(
                stage_name=normalized_stage_name,
                step_name=str(step),
                current=int(current),
                total=int(total),
                elapsed_seconds=elapsed_seconds,
                strategy=str(strategy),
                total_chunks=total_chunks,
            )
            cb_raw(
                f"{stage_name}:{step}",
                int(extra.get("current_display") or current),
                int(extra.get("total_display") or total),
                str(extra.get("step_label") or ""),
                extra,
            )

        def record_stage_result(stage_result: Any) -> None:
            task = _tasks.get(task_id) or {}
            payload_state = dict(task.get("resume_payload") or base_resume_payload)
            payload_state.update({
                "kind": "kg_build",
                "strategy": strategy,
                "build_signature": build_signature,
                "total_chunks": total_chunks,
                "source": payload.get("source"),
                "mongo_doc_ids": list(payload.get("mongo_doc_ids") or []),
            })
            if getattr(stage_result, "stage", "") == "ea_recognition":
                payload_state["ea_pairs"] = module._serialize_ea_pairs(stage_result.ea_pairs)
                payload_state["stage1_rounds"] = int(getattr(stage_result, "rounds", 0) or 0)
                payload_state["resume_from_stage"] = "ea_recognition"
            elif getattr(stage_result, "stage", "") == "relation_extraction":
                payload_state["triplets"] = module._serialize_triplets(stage_result.triplets)
                payload_state["stage2_rounds"] = int(getattr(stage_result, "rounds", 0) or 0)
                payload_state["resume_from_stage"] = "triplet_optimization"
            elif getattr(stage_result, "stage", "") == "triplet_optimization":
                payload_state["triplets"] = module._serialize_triplets(stage_result.triplets)
                payload_state["stage3_rounds"] = int(getattr(stage_result, "rounds", 0) or 0)
                payload_state.pop("stage3_checkpoint", None)
                payload_state.pop("stage4_checkpoint", None)
                payload_state["resume_from_stage"] = "cross_document_fusion"
            elif getattr(stage_result, "stage", "") == "cross_document_fusion":
                payload_state.pop("stage4_checkpoint", None)
                payload_state["resume_from_stage"] = "completed"
            _task_update(task_id, resume_payload=payload_state)

        def record_stage_checkpoint(stage_name: str, checkpoint_payload: Dict[str, Any]) -> None:
            if str(stage_name) not in {"triplet_optimization", "cross_document_fusion"} or not isinstance(checkpoint_payload, dict):
                return
            task = _tasks.get(task_id) or {}
            payload_state = dict(task.get("resume_payload") or base_resume_payload)
            payload_state.update({
                "kind": "kg_build",
                "strategy": strategy,
                "build_signature": build_signature,
                "total_chunks": total_chunks,
                "source": payload.get("source"),
                "mongo_doc_ids": list(payload.get("mongo_doc_ids") or []),
            })
            if str(stage_name) == "triplet_optimization":
                payload_state["resume_from_stage"] = "triplet_optimization"
                payload_state["stage3_checkpoint"] = dict(checkpoint_payload)
            else:
                payload_state["resume_from_stage"] = "cross_document_fusion"
                payload_state["stage4_checkpoint"] = dict(checkpoint_payload)
            _task_update(task_id, resume_payload=payload_state)
            _update_task_progress_from_stage_checkpoint(
                task_id,
                str(stage_name),
                checkpoint_payload,
                elapsed_seconds=(datetime.now() - start_time).total_seconds(),
                strategy=str(strategy),
                total_chunks=total_chunks,
            )

        result = module.build_kg(
            chunks,
            enable_fusion=payload.get("enable_fusion"),
            progress_callback=kg_cb,
            resume_artifacts=resume_candidate,
            stage_result_callback=record_stage_result,
            stage_checkpoint_callback=record_stage_checkpoint,
        )

        end_time = datetime.now()
        build_time = (end_time - start_time).total_seconds()
        if active_stage_name is not None:
            stage_durations_seconds[active_stage_name] = max(
                0.0,
                (end_time - active_stage_started_at).total_seconds(),
            )

        stage_timings = _build_kg_stage_timings_payload(
            stage_durations_seconds=stage_durations_seconds,
            total_chunks=total_chunks,
            stage_results=result.stages,
        )

        result_dict = {
            "total_entities": result.total_entities,
            "total_relations": result.total_relations,
            "total_triplets": result.total_triplets,
            "nodes_written": result.nodes_written,
            "edges_written": result.edges_written,
            "stages": [
                {"stage": s.stage, "rounds": s.rounds,
                 "ea_pairs_count": len(s.ea_pairs),
                 "triplets_count": len(s.triplets), "stats": s.stats}
                for s in result.stages
            ],
            "fusion_stats": result.fusion_stats,
            "quality_metrics": result.quality_metrics,
            "stage_timings": stage_timings,
        }

        _task_update(
            task_id,
            status=TaskStatus.COMPLETED,
            result=result_dict,
            resume_payload={
                "kind": "kg_build",
                "strategy": strategy,
                "build_signature": build_signature,
                "resume_from_stage": "completed",
                "total_chunks": total_chunks,
                "source": payload.get("source"),
                "mongo_doc_ids": list(payload.get("mongo_doc_ids") or []),
            },
        )
        print(f"[KG][{task_id}] 完成: 实体={result.total_entities}, 关系={result.total_relations}, 三元组={result.total_triplets}, 耗时={build_time:.1f}s", flush=True)

        if save_to_history:
            build_id = str(uuid.uuid4())
            with _kg_history_lock:
                _kg_build_history[build_id] = {
                    "build_id": build_id,
                    "strategy": strategy,
                    "experiment_label": experiment_label,
                    "timestamp": datetime.now().isoformat(),
                    "build_time_seconds": build_time,
                    "chunk_count": total_chunks,
                    "stage_timings": stage_timings,
                    "result": result_dict,
                }
            logger.info(f"KG build saved to history: {build_id}")

    except Exception as e:
        logger.error(f"KG build failed: {e}", exc_info=True)
        print(f"[KG][{task_id}] 失败\n  错误: {e}", flush=True)
        _task_update(task_id, status=TaskStatus.FAILED, error=str(e))
    finally:
        with _kg_worker_lock:
            _kg_workers.pop(task_id, None)


def _start_kg_worker(
    task_id: str, payload: Dict[str, Any], loop: Optional[asyncio.AbstractEventLoop]
) -> None:
    with _kg_worker_lock:
        worker = _kg_workers.get(task_id)
        if worker and worker.is_alive():
            return
        worker = threading.Thread(
            target=_kg_task_worker,
            args=(task_id, payload, loop),
            daemon=True,
            name=f"kg-task-{task_id}",
        )
        _kg_workers[task_id] = worker
        worker.start()


def _resume_vector_tasks_on_startup() -> None:
    with _tasks_lock:
        candidates: List[tuple[str, Dict[str, Any]]] = []
        for tid, task in _tasks.items():
            if task.get("module") != "vector":
                continue
            payload = task.get("resume_payload")
            if not isinstance(payload, dict):
                continue
            st = _status_value(task.get("status"))
            if st not in {"pending", "running", "waiting_network"}:
                continue
            candidates.append((tid, payload))
    for tid, payload in candidates:
        _start_vector_worker(tid, payload, loop=None)


def _resume_kg_tasks_on_startup() -> None:
    with _tasks_lock:
        candidates: List[tuple[str, Dict[str, Any]]] = []
        for tid, task in _tasks.items():
            if not _kg_task_is_resumable(task):
                continue
            st = _status_value(task.get("status"))
            if st not in {"pending", "running", "waiting_network"}:
                continue
            candidates.append((tid, dict(task.get("request_payload") or {})))
    for tid, payload in candidates:
        _start_kg_worker(tid, payload, loop=None)


_load_tasks()
_save_tasks()
_resume_vector_tasks_on_startup()
_resume_kg_tasks_on_startup()


# ============================================================
# WebSocket: 实时进度
# ============================================================

@router.websocket("/ws/progress/{task_id}")
async def ws_progress(websocket: WebSocket, task_id: str):
    """WebSocket 端点，客户端连接后接收 ProgressUpdate JSON 消息。"""
    await websocket.accept()
    _ws_connections[task_id] = websocket
    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                await websocket.send_json({"heartbeat": True, "task_id": task_id})
            except WebSocketDisconnect:
                break

            task = _tasks.get(task_id)
            if task and task["status"] in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
                progress = task.get("progress") if isinstance(task.get("progress"), dict) else {}
                progress_extra = progress.get("extra") if isinstance(progress.get("extra"), dict) else {}
                terminal_extra = dict(progress_extra)
                terminal_extra.update({
                    "result": task.get("result"),
                    "error": task.get("error"),
                })
                await websocket.send_json({
                    "task_id": task_id, "module": task["module"],
                    "stage": "done", "current": 1, "total": 1,
                    "message": task["status"],
                    "extra": terminal_extra,
                })
                break
    finally:
        _ws_connections.pop(task_id, None)


# ============================================================
# 文件上传
# ============================================================

@router.post("/upload", response_model=UploadedFileInfo)
async def upload_file(
    file: UploadFile = File(...),
    category: str = Form(default=""),
):
    """上传 PDF 文件，返回保存路径。"""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")

    cat_dir = UPLOAD_DIR / (category or "uncategorized")
    cat_dir.mkdir(parents=True, exist_ok=True)
    dest = cat_dir / file.filename

    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Avoid leaking absolute server paths to clients.
    rel = _doc_path_for(category or "uncategorized", file.filename)
    return UploadedFileInfo(
        filename=file.filename,
        size_bytes=dest.stat().st_size,
        saved_path=rel,
        category=category,
    )


# ============================================================
# 模块1: OCR 端点
# ============================================================

@router.get("/ocr/list")
async def ocr_list(category: str = Query(default="")):
    """扫描 documents/ 和 documents_ocr/ 目录，返回文件列表及状态。

    逻辑:
    - 遍历 documents/{category}/*.pdf 得到所有待处理文件
    - 检查 documents_ocr/{category}/{stem}/ 是否存在 .md 文件判断是否已完成
    - 已完成的读取页数和图片数
    """
    if category and category not in CATEGORIES:
        raise HTTPException(400, f"Invalid category. Must be one of: {CATEGORIES}")

    categories = [category] if category else CATEGORIES
    items = []
    idx = 0
    for cat in categories:
        cat_src = DOCUMENTS_DIR / cat
        cat_ocr = DOCUMENTS_OCR_DIR / cat
        if not cat_src.exists():
            continue
        for pdf in sorted(cat_src.glob("*.pdf")):
            idx += 1
            stem = pdf.stem
            doc_path = _doc_path_for(cat, pdf.name)
            status = "pending"
            total_pages = 0
            success_pages = 0
            image_count = 0

            # 检查 OCR 输出
            ocr_dir = cat_ocr / stem
            md_file = None
            if ocr_dir.exists():
                md_files = list(ocr_dir.rglob("*.md"))
                if md_files:
                    md_file = sorted(md_files, key=lambda p: p.name.lower())[0]
                    status = "completed"

            # 尝试读取页数
            if status == "completed":
                # 从 content_list.json 或 middle.json 读取页数
                for pat in ("*_content_list.json", "*_middle.json", "*.json"):
                    json_files = list(ocr_dir.rglob(pat))
                    if json_files:
                        try:
                            import json as _json
                            data = _json.loads(json_files[0].read_text(encoding="utf-8", errors="ignore"))
                            if isinstance(data, dict) and isinstance(data.get("pages"), list):
                                total_pages = len(data["pages"])
                                success_pages = total_pages
                            elif isinstance(data, list):
                                page_ids = {item.get("page_idx", 0) for item in data if isinstance(item, dict)}
                                total_pages = max(page_ids) + 1 if page_ids else 0
                                success_pages = total_pages
                                image_count = sum(1 for item in data if isinstance(item, dict) and str(item.get("type", "")).lower() == "image")
                        except Exception:
                            pass
                        break

                # 若 json 没读到页数，尝试从 PDF 本身读
                if total_pages == 0:
                    try:
                        from pypdf import PdfReader
                        total_pages = len(PdfReader(str(pdf)).pages)
                        success_pages = total_pages
                    except Exception:
                        pass
            else:
                # Merge best-effort runtime task status (so refresh keeps running/failed).
                latest = _find_latest_task_for_doc("ocr", doc_path)
                if latest:
                    _, t = latest
                    st_raw = t.get("status")
                    st = (st_raw.value if isinstance(st_raw, TaskStatus) else str(st_raw or "")).lower()
                    if st == "running":
                        status = "running"
                    elif st == "failed":
                        status = "failed"

            items.append({
                "id": idx,
                "filename": pdf.name,
                "category": cat,
                "file_path": doc_path,
                "status": status,
                "total_pages": total_pages,
                "success_pages": success_pages,
                "image_count": image_count,
                "ocr_dir": _doc_path_for(cat, stem) if status == "completed" else None,
            })

    return {"items": items, "total": len(items)}


@router.post("/ocr/upload-to-documents")
async def ocr_upload_to_documents(
    file: UploadFile = File(...),
    category: str = Form(...),
):
    """上传 PDF 到 documents/{category}/ 目录。"""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")
    if category not in CATEGORIES:
        raise HTTPException(400, f"Invalid category. Must be one of: {CATEGORIES}")

    cat_dir = DOCUMENTS_DIR / category
    cat_dir.mkdir(parents=True, exist_ok=True)
    dest = cat_dir / file.filename

    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Avoid leaking absolute server paths to clients.
    rel = _doc_path_for(category, file.filename)
    return UploadedFileInfo(
        filename=file.filename,
        size_bytes=dest.stat().st_size,
        saved_path=rel,
        category=category,
    )


@router.post("/ocr/process", response_model=TaskResponse)
async def ocr_process(req: OcrRequest, background_tasks: BackgroundTasks):
    """异步 OCR 处理，返回 task_id。"""
    doc_path, cat, abs_pdf = _resolve_pdf_under_documents(req.file_path, req.category)

    if not getattr(req, "force", False) and _is_ocr_completed(doc_path, cat):
        raise HTTPException(409, "OCR already completed for this file. Use force=true to rerun.")

    # Deduplicate + create task atomically (avoid double-start race).
    with _tasks_lock:
        latest = _find_latest_task_for_doc("ocr", doc_path)
        if latest:
            st_raw = latest[1].get("status")
            st = (st_raw.value if isinstance(st_raw, TaskStatus) else str(st_raw or "")).lower()
        else:
            st = ""
        if latest and st == "running":
            return TaskResponse(task_id=latest[0], status=TaskStatus.RUNNING)

        task_id = _new_task("ocr")
        _task_update(task_id, doc_path=doc_path)
    loop = asyncio.get_running_loop()

    def run():
        from data_process.ocr.ocr_module import OcrModule
        print(f"[OCR][{task_id}] 开始处理: {doc_path}", flush=True)
        try:
            _task_update(task_id, status=TaskStatus.RUNNING)
            with _ocr_semaphore:
                module = OcrModule(output_dir=str(DOCUMENTS_OCR_DIR))
                page_range = None
                if req.page_start and req.page_end:
                    page_range = (req.page_start, req.page_end)
                cb = _sync_progress_factory(task_id, "ocr", loop)
                result = module.process_pdf(
                    pdf_path=str(abs_pdf), category=cat,
                    page_range=page_range, progress_callback=cb,
                )
                # Store only summary fields to avoid memory blow-ups on large docs.
                _task_update(
                    task_id,
                    status=TaskStatus.COMPLETED,
                    result={
                        "file_name": result.file_name,
                        "total_pages": result.total_pages,
                        "success_pages": result.success_pages,
                        "duration_ms": result.duration_ms,
                        "artifacts_dir": _to_relative_ocr_dir(result.artifacts_dir),
                    },
                )
                print(f"[OCR][{task_id}] 完成: {result.file_name} ({result.success_pages}/{result.total_pages} 页, {result.duration_ms}ms)", flush=True)
        except Exception as e:
            print(f"[OCR][{task_id}] 失败: {doc_path}\n  错误: {e}", flush=True)
            _task_update(task_id, status=TaskStatus.FAILED, error=str(e))

    background_tasks.add_task(asyncio.to_thread, run)
    return TaskResponse(task_id=task_id, status=TaskStatus.PENDING)


@router.post("/ocr/process-sync", response_model=OcrResultResponse)
async def ocr_process_sync(req: OcrRequest):
    """同步 OCR 处理 (小文件/测试用)。"""
    _doc_path, cat, abs_pdf = _resolve_pdf_under_documents(req.file_path, req.category)

    from data_process.ocr.ocr_module import OcrModule
    module = OcrModule(output_dir=str(DOCUMENTS_OCR_DIR))
    page_range = None
    if req.page_start and req.page_end:
        page_range = (req.page_start, req.page_end)
    result = await asyncio.to_thread(
        module.process_pdf, str(abs_pdf), cat, page_range
    )
    return OcrResultResponse(
        file_name=result.file_name, markdown=result.markdown,
        detail=result.detail, total_pages=result.total_pages,
        success_pages=result.success_pages, duration_ms=result.duration_ms,
        artifacts_dir=_to_relative_ocr_dir(result.artifacts_dir),
    )


# ============================================================
# 模块2: 向量化端点
# ============================================================

@router.post("/vector/process", response_model=TaskResponse)
async def vector_process(req: VectorizeRequest, background_tasks: BackgroundTasks):
    """异步向量化处理，返回 task_id。"""
    if not getattr(req, "force", False):
        doc_path = str((req.doc_metadata or {}).get("file_path") or "").strip()
        if doc_path:
            vectorized_map = _load_vectorized_document_index()
            if doc_path in vectorized_map:
                raise HTTPException(409, "Vectorization already completed for this file. Use force=true to rerun.")

    doc_path = _norm_doc_path(str((req.doc_metadata or {}).get("file_path") or ""))
    with _tasks_lock:
        if doc_path:
            latest = _find_latest_task_for_doc("vector", doc_path)
            if latest:
                st = _status_value(latest[1].get("status"))
                if st in {"running", "waiting_network"}:
                    return TaskResponse(task_id=latest[0], status=TaskStatus(st))
        task_id = _new_task("vector")
        if doc_path:
            _task_update(task_id, doc_path=doc_path)

    loop = asyncio.get_running_loop()
    payload = {
        "kind": "direct",
        "ocr_result": req.ocr_result,
        "doc_metadata": req.doc_metadata,
        "force": bool(getattr(req, "force", False)),
    }
    _start_vector_worker(task_id, payload, loop)
    return TaskResponse(task_id=task_id, status=TaskStatus.PENDING)


@router.get("/vector/list")
async def vector_list(category: str = ""):
    """返回向量化列表（包含全部文档，按向量化可执行状态控制操作）。"""
    items = []
    idx = 0
    vectorized_map = _load_vectorized_document_index()

    if category and category not in CATEGORIES:
        raise HTTPException(400, f"Invalid category. Must be one of: {CATEGORIES}")

    categories = [category] if category else CATEGORIES

    for cat in categories:
        cat_src = DOCUMENTS_DIR / cat
        cat_ocr = DOCUMENTS_OCR_DIR / cat
        if not cat_src.exists():
            continue
        for pdf in sorted(cat_src.glob("*.pdf")):
            stem = pdf.stem
            ocr_dir = cat_ocr / stem

            has_ocr_output = ocr_dir.exists() and bool(list(ocr_dir.rglob("*.md")))
            if not has_ocr_output:
                continue
            readiness = _evaluate_ocr_readiness(pdf, ocr_dir)
            ocr_ready = bool(readiness.get("ready"))

            idx += 1
            file_path = str(pdf.resolve())
            doc_path = _doc_path_for(cat, pdf.name)
            vector_info = vectorized_map.get(file_path) or vectorized_map.get(doc_path)
            status = "completed" if vector_info else "pending"
            retry_count = 0
            next_retry_at = None
            if not vector_info:
                latest = _find_latest_task_for_doc("vector", doc_path)
                if latest:
                    _, t = latest
                    st_raw = t.get("status")
                    st = (st_raw.value if isinstance(st_raw, TaskStatus) else str(st_raw or "")).lower()
                    if st == "running":
                        status = "running"
                    elif st == "waiting_network":
                        status = "waiting_network"
                        retry_count = int(t.get("retry_count") or 0)
                        next_retry_at = t.get("next_retry_at")
                    elif st == "failed":
                        status = "failed"

            ocr_payload = (
                readiness.get("payload")
                if isinstance(readiness, dict)
                else {"result": {}}
            )
            result = ocr_payload.get("result", {})
            detail = result.get("detail") if isinstance(result.get("detail"), list) else []
            image_count = sum(
                1 for it in detail
                if isinstance(it, dict) and str(it.get("type", "")).lower() == "image"
            )

            items.append({
                "id": idx,
                "filename": pdf.name,
                "category": cat,
                "file_path": file_path,
                "doc_path": doc_path,
                "ocr_dir": (
                    str((readiness.get("artifacts_dir") or ocr_dir).resolve())
                    if ocr_dir.exists()
                    else None
                ),
                "status": status,
                "can_vectorize": bool(ocr_ready and not vector_info),
                "total_pages": int(result.get("total_page_number") or 0),
                "success_pages": int(result.get("success_count") or 0),
                "image_count": image_count,
                "vector_doc_id": (vector_info or {}).get("doc_id"),
                "total_chunks": ((vector_info or {}).get("statistics") or {}).get("total_chunks", 0),
                "version": int((vector_info or {}).get("version", 0)),
                "retry_count": int(retry_count),
                "next_retry_at": next_retry_at,
                "ocr_ready": bool(ocr_ready),
                "ocr_reason": str(readiness.get("reason") or ""),
                "pdf_pages": int(readiness.get("pdf_pages") or 0),
            })

    return {"items": items, "total": len(items)}


@router.delete("/ocr/file")
async def ocr_delete_file(
    file_path: str = Query(..., description="文档相对路径，如 标准规范/a.pdf"),
    category: str = Query(default="", description="可选，分类校验"),
):
    """删除单个 OCR 源文档及其 OCR 产物目录。"""
    doc_path, cat, abs_pdf = _resolve_pdf_under_documents(file_path, category)
    ocr_dir = DOCUMENTS_OCR_DIR / cat / abs_pdf.stem

    abs_pdf.unlink(missing_ok=False)
    if ocr_dir.exists() and ocr_dir.is_dir():
        shutil.rmtree(ocr_dir, ignore_errors=True)

    # 清理 OCR 产物缓存（若存在）
    try:
        cache_key = str(ocr_dir.resolve())
    except Exception:
        cache_key = str(ocr_dir)
    with _ocr_artifacts_cache_lock:
        _ocr_artifacts_cache.pop(cache_key, None)

    # 清理该文件的 OCR 任务记录，避免列表状态被历史失败/运行任务污染。
    with _tasks_lock:
        stale_ids = [
            task_id
            for task_id, task in _tasks.items()
            if task.get("module") == "ocr" and _norm_doc_path(str(task.get("doc_path") or "")) == doc_path
        ]
        for task_id in stale_ids:
            _tasks.pop(task_id, None)

    return {"deleted": True, "doc_path": doc_path, "ocr_dir_removed": _doc_path_for(cat, abs_pdf.stem)}


@router.post("/vector/process-from-ocr", response_model=TaskResponse)
async def vector_process_from_ocr(req: VectorizeFromOcrRequest, background_tasks: BackgroundTasks):
    """根据 OCR 落盘产物直接启动向量化任务。"""
    doc_path, cat, abs_pdf = _resolve_pdf_under_documents(req.file_path, req.category)
    ocr_dir = DOCUMENTS_OCR_DIR / cat / abs_pdf.stem
    readiness = _evaluate_ocr_readiness(abs_pdf, ocr_dir)
    if not readiness.get("artifacts_dir"):
        raise HTTPException(400, f"OCR artifacts not found: {ocr_dir}")
    if not readiness.get("ready"):
        reason = str(readiness.get("reason") or "")
        if reason == "ocr_partial":
            raise HTTPException(
                409,
                "OCR not fully completed for this file yet. Please finish full-document OCR first.",
            )
        raise HTTPException(409, "OCR not completed for this file yet.")

    if not getattr(req, "force", False):
        vectorized_map = _load_vectorized_document_index()
        # document_id may be stored as either absolute path or doc_path; check both.
        if str(abs_pdf) in vectorized_map or doc_path in vectorized_map:
            raise HTTPException(409, "Vectorization already completed for this file. Use force=true to rerun.")
    resume_payload = {
        "kind": "from_ocr",
        "doc_path": doc_path,
        "category": cat,
        "title": req.title or abs_pdf.stem,
        "force": bool(getattr(req, "force", False)),
    }

    with _tasks_lock:
        latest = _find_latest_task_for_doc("vector", doc_path)
        if latest:
            st = _status_value(latest[1].get("status"))
            if st in {"running", "waiting_network"}:
                return TaskResponse(task_id=latest[0], status=TaskStatus(st))
        task_id = _new_task("vector")
        _task_update(task_id, doc_path=doc_path, resume_payload=resume_payload)

    loop = asyncio.get_running_loop()
    _start_vector_worker(task_id, resume_payload, loop)
    return TaskResponse(task_id=task_id, status=TaskStatus.PENDING)


@router.get("/pipeline/overview")
async def pipeline_overview():
    """聚合上传/OCR/向量化/KG 状态，供前端一屏总览。"""
    items = []
    uploaded_total = 0
    ocr_completed = 0
    vector_completed = 0
    vectorized_map = _load_vectorized_document_index()

    for cat in CATEGORIES:
        cat_src = DOCUMENTS_DIR / cat
        cat_ocr = DOCUMENTS_OCR_DIR / cat
        if not cat_src.exists():
            continue
        for pdf in sorted(cat_src.glob("*.pdf")):
            uploaded_total += 1
            doc_path = _doc_path_for(cat, pdf.name)
            file_path = str(pdf.resolve())
            ocr_dir = cat_ocr / pdf.stem

            ocr_status = "pending"
            if ocr_dir.exists() and bool(list(ocr_dir.rglob("*.md"))):
                ocr_status = "completed"
            else:
                latest_ocr = _find_latest_task_for_doc("ocr", doc_path)
                if latest_ocr:
                    st_raw = latest_ocr[1].get("status")
                    st = (st_raw.value if isinstance(st_raw, TaskStatus) else str(st_raw or "")).lower()
                    if st in {"running", "failed"}:
                        ocr_status = st

            vector_status = "pending"
            vector_info = vectorized_map.get(file_path) or vectorized_map.get(doc_path)
            if vector_info:
                vector_status = "completed"
            else:
                latest_vec = _find_latest_task_for_doc("vector", doc_path)
                if latest_vec:
                    st_raw = latest_vec[1].get("status")
                    st = (st_raw.value if isinstance(st_raw, TaskStatus) else str(st_raw or "")).lower()
                    if st in {"running", "waiting_network", "failed"}:
                        vector_status = st

            if ocr_status == "completed":
                ocr_completed += 1
            if vector_status == "completed":
                vector_completed += 1

            items.append({
                "doc_path": doc_path,
                "filename": pdf.name,
                "category": cat,
                "ocr_status": ocr_status,
                "vector_status": vector_status,
                "can_vectorize": ocr_status == "completed" and vector_status == "pending",
                "vector_doc_id": (vector_info or {}).get("doc_id"),
                "can_graphize": vector_status == "completed" and bool((vector_info or {}).get("doc_id")),
            })

    with _tasks_lock:
        latest_kg = _find_latest_task_for_module("kg")

    kg_payload: Dict[str, Any] = {
        "task_id": None,
        "status": "idle",
        "progress_percent": 0,
        "stage": "",
        "updated_at": None,
    }
    if latest_kg:
        task_id, task = latest_kg
        st_raw = task.get("status")
        status = st_raw.value if isinstance(st_raw, TaskStatus) else str(st_raw or "idle")
        progress = task.get("progress") or {}
        current = int(progress.get("current") or 0) if isinstance(progress, dict) else 0
        total = int(progress.get("total") or 0) if isinstance(progress, dict) else 0
        extra = progress.get("extra", {}) if isinstance(progress, dict) else {}
        percent = (
            int(extra.get("overall_percent") or 0)
            if isinstance(extra, dict) and extra.get("overall_percent") is not None
            else (int(round((current / total) * 100)) if total > 0 else (100 if status == "completed" else 0))
        )
        kg_payload = {
            "task_id": task_id,
            "status": status,
            "progress_percent": max(0, min(100, percent)),
            "stage": progress.get("stage", "") if isinstance(progress, dict) else "",
            "updated_at": task.get("created_at"),
        }

    return {
        "summary": {
            "uploaded_total": uploaded_total,
            "ocr_completed": ocr_completed,
            "vector_completed": vector_completed,
            "kg": kg_payload,
        },
        "items": items,
        "total": len(items),
    }


@router.post("/vector/rerank", response_model=RerankResultResponse)
async def vector_rerank(req: RerankRequest):
    """使用配置的 reranker 模型重排序 (同步)。"""
    reranker = _get_reranker()
    ranked = await asyncio.to_thread(
        reranker.rerank, req.query, req.chunks, req.top_k
    )
    return RerankResultResponse(query=req.query, results=ranked, total=len(ranked))


# ============================================================
# 模块3: KG 端点
# ============================================================

def _load_chunks_from_mongo(doc_ids: Optional[list] = None) -> list:
    """通过 KG builder 统一的 MongoDB 配置加载 chunks。"""
    module = _create_kg_module(strategy="B1")
    return _load_chunks_from_builder_db(module, doc_ids)


@router.post("/kg/build", response_model=TaskResponse)
async def kg_build(req: KgBuildRequest, background_tasks: BackgroundTasks):
    """异步完整 4 阶段 KG 构建，返回 task_id。

    支持策略参数:
    - strategy: 构建策略 (B0/B1/B2/B3，兼容 E1/E2/E3)
    - custom_config: 自定义配置 (当strategy="custom"时使用)
    - experiment_label: 实验标签 (用于对比实验)
    - save_to_history: 是否保存到历史记录
    """
    task_id = _new_task("kg")
    loop = asyncio.get_running_loop()
    request_payload = _kg_request_payload_from_request(req)
    _task_update(task_id, request_payload=request_payload)
    background_tasks.add_task(_start_kg_worker, task_id, request_payload, loop)
    return TaskResponse(task_id=task_id, status=TaskStatus.PENDING)


@router.post("/kg/stage", response_model=TaskResponse)
async def kg_run_stage(req: KgStageOnlyRequest, background_tasks: BackgroundTasks):
    """异步运行单个 KG 阶段 (步进式控制)。"""
    task_id = _new_task("kg")
    loop = asyncio.get_running_loop()

    def run():
        from data_process.kg.kg_module import KgModule, EAPair, Triplet
        try:
            _task_update(task_id, status=TaskStatus.RUNNING)
            module = KgModule(strategy=getattr(req, "strategy", "B1"))
            cb = _sync_progress_factory(task_id, "kg", loop)
            chunks = req.chunks or _load_chunks_from_mongo(req.mongo_doc_ids)
            result_payload: Dict[str, Any]

            if req.stage == "ea_recognition":
                result = module.stage1_ea_recognition(chunks, progress_callback=cb)
                result_payload = {
                    "stage": result.stage,
                    "ea_pairs": [
                        {"entity_name": p.entity_name, "entity_type": p.entity_type,
                         "description": p.description, "attributes": p.attributes}
                        for p in result.ea_pairs
                    ],
                    "stats": result.stats,
                }

            elif req.stage == "relation_extraction":
                ea_pairs = [EAPair(**p) for p in (req.ea_pairs or [])]
                result = module.stage2_relation_extraction(chunks, ea_pairs, progress_callback=cb)
                result_payload = {
                    "stage": result.stage,
                    "triplets": [
                        {"subject": t.subject, "relation": t.relation,
                         "object": t.object, "confidence": t.confidence}
                        for t in result.triplets
                    ],
                    "stats": result.stats,
                }

            elif req.stage == "triplet_optimization":
                triplets = [Triplet(**t) for t in (req.triplets or [])]
                ea_pairs = [EAPair(**p) for p in (req.ea_pairs or [])]
                result = module.stage3_triplet_optimization(triplets, ea_pairs, progress_callback=cb)
                result_payload = {
                    "stage": result.stage,
                    "triplets": [
                        {"subject": t.subject, "relation": t.relation,
                         "object": t.object, "confidence": t.confidence}
                        for t in result.triplets
                    ],
                    "stats": result.stats,
                }

            elif req.stage == "cross_document_fusion":
                triplets = [Triplet(**t) for t in (req.triplets or [])]
                ea_pairs = [EAPair(**p) for p in (req.ea_pairs or [])]
                result = module.stage4_cross_document_fusion(triplets, ea_pairs, progress_callback=cb)
                result_payload = {"stage": result.stage, "stats": result.stats}

            else:
                raise ValueError(f"Unknown stage: {req.stage}")

            _task_update(task_id, status=TaskStatus.COMPLETED, result=result_payload)
        except Exception as e:
            _task_update(task_id, status=TaskStatus.FAILED, error=str(e))

    background_tasks.add_task(asyncio.to_thread, run)
    return TaskResponse(task_id=task_id, status=TaskStatus.PENDING)


# ============================================================
# 任务状态查询
# ============================================================

@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """轮询任务状态和结果。"""
    with _tasks_lock:
        task = dict(_tasks.get(task_id) or {})
    if not task:
        raise HTTPException(404, f"Task {task_id} not found")
    status = _status_value(task.get("status"))
    return TaskStatusResponse(
        task_id=task_id, status=task["status"],
        progress=task.get("progress"), result=task.get("result"),
        resume_payload=task.get("resume_payload"),
        error=task.get("error"),
        error_hint=_describe_task_error(status, task.get("error")),
        created_at=task.get("created_at"),
    )


# ============================================================
# 新增: KG策略管理和历史记录
# ============================================================

# 构建历史存储 (内存版,生产环境可换数据库)
_kg_build_history: Dict[str, Dict[str, Any]] = {}
_kg_history_lock = threading.RLock()


@router.get("/kg/strategies")
async def get_kg_strategies():
    """获取所有KG构建策略配置"""
    from data_process.kg.strategy_presets import list_strategies
    return {"strategies": list_strategies()}


@router.get("/kg/history")
async def get_kg_build_history():
    """获取KG构建历史记录"""
    with _kg_history_lock:
        builds = [
            {
                "build_id": build["build_id"],
                "strategy": build["strategy"],
                "experiment_label": build.get("experiment_label"),
                "timestamp": build["timestamp"],
                "total_entities": build["result"]["total_entities"],
                "total_relations": build["result"]["total_relations"],
                "total_triplets": build["result"]["total_triplets"],
                "aof": build["result"].get("quality_metrics", {}).get("aof", 0),
                "build_time_seconds": build["build_time_seconds"],
                "chunk_count": build.get("chunk_count"),
            }
            for build in sorted(
                _kg_build_history.values(),
                key=lambda x: x["timestamp"],
                reverse=True,
            )
        ]
    return {"builds": builds}


@router.delete("/kg/history/{build_id}")
async def delete_kg_build_history(build_id: str):
    """删除指定的KG构建历史记录"""
    with _kg_history_lock:
        if build_id not in _kg_build_history:
            raise HTTPException(404, f"Build {build_id} not found")
        del _kg_build_history[build_id]
    return {"message": "Build history deleted"}


@router.delete("/kg/neo4j/clear")
async def clear_neo4j():
    """清空 KG 构建结果，但保留骨架节点和骨架关系。"""
    try:
        return _clear_kg_keep_skeleton()
    except Exception as e:
        logger.error(f"Failed to clear Neo4j: {e}", exc_info=True)
        raise HTTPException(500, f"Failed to clear Neo4j: {str(e)}")


@router.post("/kg/compare")
async def compare_kg_builds(build_ids: list[str]):
    """对比多个KG构建结果"""
    if len(build_ids) < 2:
        raise HTTPException(400, "Need at least 2 builds to compare")

    comparison = []
    with _kg_history_lock:
        for build_id in build_ids:
            if build_id not in _kg_build_history:
                raise HTTPException(404, f"Build {build_id} not found")

            build = _kg_build_history[build_id]
            result = build["result"]

            comparison.append({
                "build_id": build_id,
                "strategy": build["strategy"],
                "experiment_label": build.get("experiment_label"),
                "metrics": {
                    "total_entities": result["total_entities"],
                    "total_relations": result["total_relations"],
                    "total_triplets": result["total_triplets"],
                    "aof": result.get("quality_metrics", {}).get("aof", 0),
                    "relation_diversity": result.get("quality_metrics", {}).get("relation_diversity", 0),
                    "latent_triplets": result.get("fusion_stats", {}).get("latent_triplets_found", 0),
                    "build_time_seconds": build["build_time_seconds"],
                },
            })

    return {"comparison": comparison}
