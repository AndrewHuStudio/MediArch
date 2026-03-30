# -*- coding: utf-8 -*-
"""
VLM 图片描述回填（不重跑 OCR）

目的：
- 对 MongoDB 中已存在的 image chunks 生成/补齐 VLM 语义描述（content）
- 同步更新 Milvus 向量（让图片可被向量检索稳定命中）

为什么需要它：
- `batch_indexer --force` 会触发 OCR（你的环境里 MinerU 走远程 API，重跑成本高）
- 这里只利用已落盘的 `backend/databases/documents_ocr/**/images/*`，只补 VLM + embedding

运行示例：
  # 1) 先看会处理多少（不调用 VLM，不写库）
  python -m backend.cli.vlm_backfill_images

  # 2) 真正执行（会调用 VLM + embedding，并更新 Mongo + Milvus）
  python -m backend.cli.vlm_backfill_images --apply

  # 3) 仅处理某个资料（doc_id 可重复）
  python -m backend.cli.vlm_backfill_images --doc-id 69494a2b818c5afeb13ce2ad --apply

  # 4) 控制成本：每份资料最多处理 30 张图
  python -m backend.cli.vlm_backfill_images --max-images-per-doc 30 --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.env_loader import load_dotenv
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCS_OCR_ROOT = PROJECT_ROOT / "backend" / "databases" / "documents_ocr"
console = Console()


def _load_env() -> None:
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()


def _ensure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _parse_page_range_spec(spec: str) -> Optional[Tuple[int, int]]:
    raw = (spec or "").strip().replace(" ", "")
    if not raw:
        return None
    if "-" not in raw:
        n = int(raw)
        return (n, n)
    a, b = raw.split("-", 1)
    s, e = int(a), int(b)
    if s <= 0 or e < s:
        raise ValueError(f"invalid page range: {spec}")
    return (s, e)


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


def _resolve_image_abs(chunk: Dict[str, Any]) -> Optional[str]:
    """
    Resolve absolute image path for a chunk.

    Preferred order:
    1) image_url_abs (if present)
    2) documents_ocr root + image_url (when image_url already contains category/doc dir)
    3) documents_ocr/<doc_category>/<stem(doc_title)> + image_url (when image_url like "images/xxx.jpg")
    4) documents_ocr/<doc_category>/<stem(doc_title)>/images/<filename> (when image_url is bare filename)
    """
    abs_hint = (chunk.get("image_url_abs") or "").strip()
    if abs_hint:
        p = Path(abs_hint)
        if p.is_file():
            return str(p.resolve())

    rel = (chunk.get("image_url") or "").strip().lstrip("/\\")
    if not rel:
        return None

    # 2) already includes category/doc dir
    p1 = (DOCS_OCR_ROOT / rel).resolve()
    if p1.is_file():
        return str(p1)

    doc_category = (chunk.get("doc_category") or chunk.get("doc_type") or "").strip()
    doc_title = (chunk.get("doc_title") or chunk.get("source_document") or "").strip()
    if doc_category and doc_title:
        safe_name = Path(doc_title).stem
        doc_dir = (DOCS_OCR_ROOT / doc_category / safe_name).resolve()
        p2 = (doc_dir / rel).resolve()
        if p2.is_file():
            return str(p2)

        p3 = (doc_dir / "images" / Path(rel).name).resolve()
        if p3.is_file():
            return str(p3)

    return None


def _vlm_ok(vlm_caption: str) -> bool:
    if not isinstance(vlm_caption, str):
        return False
    parts = vlm_caption.split("] ", 1)
    return len(parts) == 2 and bool(parts[1].strip())


def _print_stats(title: str, stats: Dict[str, Any]) -> None:
    table = Table(title=title, show_header=False)
    table.add_column("k", style="cyan", no_wrap=True)
    table.add_column("v", style="white")
    for k, v in stats.items():
        table.add_row(str(k), str(v))
    console.print(table)


def main(argv: Optional[List[str]] = None) -> int:
    _load_env()
    _ensure_utf8_stdio()

    parser = argparse.ArgumentParser(description="Backfill VLM captions for image chunks (no OCR)")
    parser.add_argument("--apply", action="store_true", help="执行回填（默认仅统计，不写库/不调用 VLM）")
    parser.add_argument("--force", action="store_true", help="即使已处理（metadata.vlm_processed=true）也重新处理（将优先命中 VLM 缓存）")
    parser.add_argument("--yes", action="store_true", help="跳过确认（仅在 --apply 时生效）")
    parser.add_argument("--doc-id", action="append", default=[], help="仅处理指定 doc_id（可重复）")
    parser.add_argument("--category", default=None, help="仅处理指定 doc_category（如 书籍报告/政策文件）")
    parser.add_argument("--page-range", default=None, help="仅处理页段（如 120-160），按 page_range[0] 过滤")
    parser.add_argument("--max-images-per-doc", type=int, default=0, help="每份资料最多处理多少张图（0=不限制）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少张图（0=不限制）")
    parser.add_argument("--batch-size", type=int, default=32, help="Milvus/Embedding 同步批次大小（同一 doc 内）")
    parser.add_argument(
        "--mongo-batch-size",
        type=int,
        default=int(os.getenv("MONGO_CURSOR_BATCH_SIZE", "10") or 10),
        help="MongoDB cursor batch_size（建议 1-20；降低长任务游标/会话空闲超时风险）",
    )
    parser.add_argument("--no-milvus", action="store_true", help="仅更新 Mongo，不更新 Milvus（不建议）")
    args = parser.parse_args(argv)

    mongo_uri = (os.getenv("MONGODB_URI") or "").strip()
    mongo_db = (os.getenv("MONGODB_DATABASE") or "mediarch").strip()
    if not mongo_uri:
        raise SystemExit("missing MONGODB_URI (set env or .env)")

    page_rng = _parse_page_range_spec(args.page_range) if args.page_range else None

    doc_id_values: List[Any] = []
    for v in args.doc_id:
        doc_id_values.extend(_as_object_id_candidates(str(v)))

    from pymongo import MongoClient

    client = MongoClient(mongo_uri)
    db = client[mongo_db]
    chunks = db["mediarch_chunks"]

    q: Dict[str, Any] = {"content_type": "image", "image_url": {"$nin": [None, ""]}}
    if args.category:
        q["doc_category"] = str(args.category).strip()
    if doc_id_values:
        q["doc_id"] = {"$in": doc_id_values}
    if page_rng:
        s, e = int(page_rng[0]), int(page_rng[1])
        q["page_range.0"] = {"$gte": s, "$lte": e}
    # NOTE:
    # - historical data may have metadata.vlm_processed=true even when VLM wasn't enabled
    # - treat "already processed" as: content contains a real description after the bracket, e.g. "[图片: ...] xxx"
    apply_filter: Dict[str, Any] = {}
    if not args.force:
        apply_filter = {
            "$expr": {
                "$ne": [
                    {
                        "$regexMatch": {
                            "input": {"$ifNull": ["$content", ""]},
                            "regex": r"\]\s+\S",
                        }
                    },
                    True,
                ]
            }
        }

    try:
        total_candidates = int(chunks.count_documents({**q, **apply_filter}))
    except Exception:
        total_candidates = -1

    _print_stats(
        "VLM Backfill Plan",
        {
            "mode": "APPLY" if args.apply else "DRY_RUN",
            "doc_filter": ", ".join([str(x) for x in args.doc_id]) if args.doc_id else "(none)",
            "category_filter": args.category or "(none)",
            "page_range": args.page_range or "(all)",
            "max_images_per_doc": args.max_images_per_doc or "unlimited",
            "limit": args.limit or "unlimited",
            "candidates_estimated": total_candidates if total_candidates >= 0 else "unknown",
        },
    )

    if args.apply:
        from backend.databases.ingestion.indexing.vision_describer import get_describer, generate_image_description

        describer = get_describer()
        if not getattr(describer, "enabled", False):
            raise SystemExit("VLM not configured/enabled; set VLM_API_KEY and VLM_BASE_URL (or KG_VISION_*)")

        if not args.yes:
            resp = input("\n将调用 VLM + embedding 并更新 Mongo/Milvus，是否继续？(yes/no): ").strip().lower()
            if resp not in {"y", "yes"}:
                console.print("[yellow]已取消[/yellow]")
                return 0

        embedding_generator = None
        milvus_writer = None
        if not args.no_milvus:
            from backend.databases.ingestion.indexing.embedding import EmbeddingGenerator
            from backend.databases.ingestion.indexing.milvus_writer import MilvusWriter

            embedding_generator = EmbeddingGenerator()
            milvus_writer = MilvusWriter(
                host=os.getenv("MILVUS_HOST", "localhost"),
                port=os.getenv("MILVUS_PORT", "19530"),
            )

    projection = {
        "_id": 1,
        "chunk_id": 1,
        "doc_id": 1,
        "doc_type": 1,
        "doc_category": 1,
        "doc_title": 1,
        "source_document": 1,
        "content": 1,
        "metadata": 1,
        "section": 1,
        "page_range": 1,
        "content_type": 1,
        "image_url": 1,
        "image_url_abs": 1,
    }

    mongo_batch_size = max(1, int(args.mongo_batch_size or 1))

    max_per_doc = int(args.max_images_per_doc or 0)
    hard_limit = int(args.limit or 0)
    batch_size = max(1, int(args.batch_size))

    per_doc_done: Dict[str, int] = defaultdict(int)
    scanned = 0
    selected = 0
    missing_image = 0

    vlm_success = 0
    vlm_failed = 0
    mongo_updated = 0

    milvus_deleted = 0
    milvus_inserted = 0
    milvus_skipped = 0

    current_doc_id: Optional[str] = None
    pending_for_doc: List[Dict[str, Any]] = []

    def flush_pending(doc_id_str: str) -> None:
        nonlocal milvus_deleted, milvus_inserted, milvus_skipped, pending_for_doc
        if not pending_for_doc:
            return
        if args.no_milvus:
            pending_for_doc = []
            return

        assert embedding_generator is not None
        assert milvus_writer is not None

        texts = [(c.get("content") or "") for c in pending_for_doc]
        embeddings = embedding_generator.generate_batch(texts, batch_size=min(100, len(texts)))
        for c, emb in zip(pending_for_doc, embeddings):
            c["embedding"] = emb

        chunk_ids = [str(c.get("chunk_id") or "").strip() for c in pending_for_doc if str(c.get("chunk_id") or "").strip()]
        milvus_deleted += milvus_writer.delete_by_chunk_ids(chunk_ids)

        ok, skip = milvus_writer.insert_vectors(pending_for_doc, doc_id=doc_id_str, batch_size=500)
        milvus_inserted += int(ok)
        milvus_skipped += int(skip)
        pending_for_doc = []

    if not DOCS_OCR_ROOT.exists():
        console.print(f"[yellow]WARN[/yellow] documents_ocr 不存在: {DOCS_OCR_ROOT}")

    with client.start_session() as session:
        cursor = (
            chunks.find({**q, **apply_filter}, projection, no_cursor_timeout=True, session=session)
            .sort([("doc_id", 1), ("page_range.0", 1), ("chunk_id", 1)])
            .allow_disk_use(True)
            .batch_size(mongo_batch_size)
        )

        if not args.apply:
            # DRY_RUN: 不调用 VLM，不写库。只做筛选与路径检查。
            for ch in cursor:
                scanned += 1
                doc_id_str = str(ch.get("doc_id"))
                if max_per_doc and per_doc_done[doc_id_str] >= max_per_doc:
                    continue
                img_abs = _resolve_image_abs(ch)
                if not img_abs:
                    missing_image += 1
                    continue
                per_doc_done[doc_id_str] += 1
                selected += 1
                if hard_limit and selected >= hard_limit:
                    break

            cursor.close()
            _print_stats(
                "Dry Run Result",
                {
                    "scanned": scanned,
                    "selected": selected,
                    "missing_image_path": missing_image,
                    "unique_docs_touched": len(per_doc_done),
                },
            )
            client.close()
            return 0

        # APPLY
        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task_total = total_candidates if total_candidates > 0 else None
            task_id = progress.add_task("VLM backfill images", total=task_total)

            for ch in cursor:
                scanned += 1
                progress.advance(task_id, 1)

                doc_id_str = str(ch.get("doc_id"))
                if max_per_doc and per_doc_done[doc_id_str] >= max_per_doc:
                    continue

                img_abs = _resolve_image_abs(ch)
                if not img_abs:
                    missing_image += 1
                    continue

                if current_doc_id is None:
                    current_doc_id = doc_id_str
                if doc_id_str != current_doc_id or len(pending_for_doc) >= batch_size:
                    flush_pending(str(current_doc_id))
                    current_doc_id = doc_id_str

                meta = ch.get("metadata") if isinstance(ch.get("metadata"), dict) else {}
                caption = (meta.get("caption") or "").strip()
                section = (ch.get("section") or meta.get("section") or "").strip()
                page = meta.get("page_number") or meta.get("page") or ((ch.get("page_range") or [0])[0] if isinstance(ch.get("page_range"), list) else 0)
                try:
                    page = int(page or 0)
                except Exception:
                    page = 0

                try:
                    vlm_caption = generate_image_description(
                        image_path=img_abs,
                        ocr_text=caption,
                        section=section,
                        page=page,
                    )
                except Exception:
                    vlm_caption = f"[图片: {caption}]" if caption else "[图片]"

                ok = _vlm_ok(vlm_caption)
                if ok:
                    vlm_success += 1
                else:
                    vlm_failed += 1

                new_meta = dict(meta)
                new_meta["vlm_processed"] = bool(ok)

                try:
                    res = chunks.update_one(
                        {"_id": ch["_id"]},
                        {"$set": {"content": vlm_caption, "metadata": new_meta}},
                        session=session,
                    )
                    if getattr(res, "modified_count", 0):
                        mongo_updated += 1
                except Exception:
                    pass

                # prepare for Milvus sync
                ch["content"] = vlm_caption
                ch["metadata"] = new_meta
                pending_for_doc.append(ch)
                per_doc_done[doc_id_str] += 1
                selected += 1

                if hard_limit and selected >= hard_limit:
                    break

            if current_doc_id is not None:
                flush_pending(str(current_doc_id))

        cursor.close()

    _print_stats(
        "Apply Result",
        {
            "scanned": scanned,
            "processed": selected,
            "missing_image_path": missing_image,
            "vlm_success": vlm_success,
            "vlm_failed_or_fallback": vlm_failed,
            "mongo_updated": mongo_updated,
            "milvus_deleted_est": milvus_deleted if not args.no_milvus else "(skipped)",
            "milvus_inserted": milvus_inserted if not args.no_milvus else "(skipped)",
            "milvus_skipped": milvus_skipped if not args.no_milvus else "(skipped)",
            "unique_docs_touched": len(per_doc_done),
        },
    )

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
