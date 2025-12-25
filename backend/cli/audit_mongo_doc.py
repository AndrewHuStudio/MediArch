"""
MongoDB 文档审计（CLI）

用途：
- 快速检查某个 doc_id 的 chunks 分布（text/image、是否有 image_url、VLM 覆盖情况）
- 定位“某页/某图题”的图片 chunk 是否存在（用于调试“要图但没命中”）

示例：
  python -m backend.cli.audit_mongo_doc --doc-id 6946a9e801a4654f760b5fdf
  python -m backend.cli.audit_mongo_doc --doc-id 6946a9e801a4654f760b5fdf --search "中毒科"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_env() -> None:
    """Load .env from project root if present."""
    try:
        from dotenv import load_dotenv

        env_path = PROJECT_ROOT / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except Exception:
        pass


def _ensure_utf8_stdio() -> None:
    """Best-effort: avoid Windows GBK console crashing on Unicode output (e.g. VLM bullets like '▶')."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _as_object_id_candidates(value: str) -> List[Any]:
    from bson import ObjectId

    out: List[Any] = []
    v = (value or "").strip()
    if not v:
        return out
    out.append(v)
    try:
        out.append(ObjectId(v))
    except Exception:
        pass
    # de-dup while preserving order
    uniq: List[Any] = []
    seen: set[tuple[str, str]] = set()
    for item in out:
        key = (type(item).__name__, str(item))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    return uniq


def _safe_regex(pattern: str) -> re.Pattern:
    raw = (pattern or "").strip()
    if not raw:
        raise ValueError("search pattern is empty")
    return re.compile(re.escape(raw), re.IGNORECASE)


def _print_kv(title: str, data: Dict[str, Any]) -> None:
    sys.stdout.write("\n" + "=" * 80 + "\n")
    sys.stdout.write(title + "\n")
    sys.stdout.write("=" * 80 + "\n")
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _vlm_caption_ok(content: Any) -> bool:
    """
    Heuristic: consider a VLM caption "ok" only when it contains a description part,
    e.g. "[图片: xxx] 该平面图展示了..." (must have "] " + non-empty tail).
    """
    if not isinstance(content, str):
        return False
    parts = content.split("] ", 1)
    return len(parts) == 2 and bool(parts[1].strip())


def main(argv: Optional[List[str]] = None) -> int:
    _load_env()
    _ensure_utf8_stdio()

    parser = argparse.ArgumentParser(description="Audit MongoDB chunks for a document")
    parser.add_argument("--doc-id", required=True, help="Mongo documents/_id 或 documents.document_id")
    parser.add_argument("--db", default=os.getenv("MONGODB_DATABASE", "mediarch"), help="Mongo database name")
    parser.add_argument("--uri", default=os.getenv("MONGODB_URI", ""), help="Mongo URI (default from env)")
    parser.add_argument("--base-url", default="", help="若提供，将输出可直接打开的 image_url_full")
    parser.add_argument("--search", default=None, help="在图片 caption/section/content 中搜索关键词（regex-escape）")
    parser.add_argument("--limit", type=int, default=20, help="搜索结果最多显示多少条")
    args = parser.parse_args(argv)

    uri = (args.uri or "").strip()
    if not uri:
        raise SystemExit("missing MONGODB_URI (set env or pass --uri)")

    from pymongo import MongoClient

    client = MongoClient(uri)
    db = client[str(args.db)]
    chunks = db["mediarch_chunks"]

    doc_id_values = _as_object_id_candidates(str(args.doc_id))
    if not doc_id_values:
        raise SystemExit("invalid --doc-id")

    base_q: Dict[str, Any] = {"doc_id": {"$in": doc_id_values}}

    total = chunks.count_documents(base_q)
    text_total = chunks.count_documents({**base_q, "content_type": "text"})
    img_total = chunks.count_documents({**base_q, "content_type": "image"})
    img_with_url = chunks.count_documents({**base_q, "content_type": "image", "image_url": {"$nin": [None, ""]}})
    # NOTE: historical data may have metadata.vlm_processed=true even when VLM wasn't enabled.
    # Keep both a "flag" metric and an "actual caption ok" metric.
    img_vlm_flag_true = chunks.count_documents({**base_q, "content_type": "image", "metadata.vlm_processed": True})
    img_vlm_ok = chunks.count_documents(
        {
            **base_q,
            "content_type": "image",
            "$expr": {
                "$regexMatch": {
                    "input": {"$ifNull": ["$content", ""]},
                    "regex": r"\]\s+\S",
                }
            },
        }
    )
    img_long = chunks.count_documents(
        {
            **base_q,
            "content_type": "image",
            "$expr": {"$gt": [{"$strLenCP": {"$ifNull": ["$content", ""]}}, 80]},
        }
    )

    stats = {
        "doc_id": str(args.doc_id),
        "chunks_total": total,
        "text_chunks": text_total,
        "image_chunks": img_total,
        "image_with_url": img_with_url,
        "image_vlm_flag_true": img_vlm_flag_true,
        "image_vlm_ok": img_vlm_ok,
        "image_vlm_flag_true_but_not_ok": max(0, int(img_vlm_flag_true) - int(img_vlm_ok)),
        "image_content_len_gt_80": img_long,
    }
    _print_kv("MongoDB Doc Chunk Stats", stats)

    if args.search:
        rx = _safe_regex(str(args.search))
        base_url = str(args.base_url or "").rstrip("/")
        q = {
            **base_q,
            "content_type": "image",
            "image_url": {"$nin": [None, ""]},
            "$or": [
                {"metadata.caption": {"$regex": rx.pattern, "$options": "i"}},
                {"metadata.section": {"$regex": rx.pattern, "$options": "i"}},
                {"section": {"$regex": rx.pattern, "$options": "i"}},
                {"content": {"$regex": rx.pattern, "$options": "i"}},
            ],
        }
        projection = {
            "_id": 0,
            "chunk_id": 1,
            "page_range": 1,
            "section": 1,
            "image_url": 1,
            "metadata": 1,
            "content": 1,
        }
        cursor = chunks.find(q, projection).sort([("page_range.0", 1), ("chunk_id", 1)]).limit(int(max(1, args.limit)))

        hits: List[Dict[str, Any]] = []
        for doc in cursor:
            meta = doc.get("metadata") or {}
            if not isinstance(meta, dict):
                meta = {}
            rel = doc.get("image_url")
            full = ""
            if base_url and rel:
                full = f"{base_url}/api/v1/documents/image?path={quote(str(rel))}"
            hits.append(
                {
                    "chunk_id": doc.get("chunk_id"),
                    "page_range": doc.get("page_range"),
                    "caption": meta.get("caption") or meta.get("section") or doc.get("section"),
                    "vlm_flag": meta.get("vlm_processed"),
                    "vlm_ok": _vlm_caption_ok(doc.get("content")),
                    "image_url": doc.get("image_url"),
                    "image_url_full": full or None,
                    "content_len": len((doc.get("content") or "")),
                    "content_head": (doc.get("content") or "")[:160],
                }
            )

        _print_kv(f"Image Search Hits (search={args.search})", {"count": len(hits), "hits": hits})

    client.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        sys.stderr.write("\n[WARN] interrupted\n")
        raise SystemExit(130)
    except Exception as exc:
        sys.stderr.write(f"\n[FAIL] {exc}\n")
        raise SystemExit(1)
