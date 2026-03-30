# -*- coding: utf-8 -*-
"""
VLM 覆盖率报告（按资料统计）

用途：
- 一键查看哪些资料“图片已做 VLM”（以图片 chunk 的 `content` 是否包含有效描述为准）
- 哪些资料还没做/覆盖率低（方便你分批选择资料做 VLM 回填，避免全量卡很久）

示例：
  # 列出所有资料的图片 VLM 覆盖率
  python -m backend.cli.vlm_doc_status

  # 只看未完成的资料
  python -m backend.cli.vlm_doc_status --only-missing

  # 只看覆盖率 < 60% 的资料
  python -m backend.cli.vlm_doc_status --min-ratio 0.6
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.env_loader import load_dotenv
from rich.console import Console
from rich.table import Table


PROJECT_ROOT = Path(__file__).resolve().parents[2]
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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _ratio(vlm_ok: int, with_url: int) -> Optional[float]:
    if with_url <= 0:
        return None
    return float(vlm_ok) / float(with_url)


def _fmt_ratio(r: Optional[float]) -> str:
    if r is None:
        return "-"
    return f"{r*100:.1f}%"


def _load_image_stats(chunks_coll) -> Dict[str, Dict[str, int]]:
    """
    Aggregate image chunk stats by doc_id (stringified).

    Returns:
        { doc_id_str: {image_chunks, image_with_url, image_vlm_ok, image_vlm_flag_true, image_content_len_gt_80}, ... }
    """
    pipeline = [
        {"$match": {"content_type": "image"}},
        {"$addFields": {"doc_id_str": {"$toString": "$doc_id"}}},
        {
            "$addFields": {
                "has_url": {
                    "$cond": [
                        {"$and": [{"$ne": ["$image_url", None]}, {"$ne": ["$image_url", ""]}]},
                        1,
                        0,
                    ]
                },
                # Historical data may have metadata.vlm_processed=true even if VLM was not enabled.
                # We treat "vlm_ok" as: content contains a real description after the bracket, e.g. "[图片: ...] xxx".
                "vlm_flag_true": {"$cond": [{"$eq": ["$metadata.vlm_processed", True]}, 1, 0]},
                "vlm_ok": {
                    "$cond": [
                        {
                            "$regexMatch": {
                                "input": {"$ifNull": ["$content", ""]},
                                "regex": r"\]\s+\S",
                            }
                        },
                        1,
                        0,
                    ]
                },
                "content_len_gt_80": {
                    "$cond": [
                        {
                            "$gt": [
                                {"$strLenCP": {"$ifNull": ["$content", ""]}},
                                80,
                            ]
                        },
                        1,
                        0,
                    ]
                },
            }
        },
        {
            "$group": {
                "_id": "$doc_id_str",
                "image_chunks": {"$sum": 1},
                "image_with_url": {"$sum": "$has_url"},
                "image_vlm_ok": {"$sum": "$vlm_ok"},
                "image_vlm_flag_true": {"$sum": "$vlm_flag_true"},
                "image_content_len_gt_80": {"$sum": "$content_len_gt_80"},
            }
        },
    ]
    out: Dict[str, Dict[str, int]] = {}
    for row in chunks_coll.aggregate(pipeline, allowDiskUse=True):
        doc_id_str = str(row.get("_id") or "").strip()
        if not doc_id_str:
            continue
        out[doc_id_str] = {
            "image_chunks": _safe_int(row.get("image_chunks")),
            "image_with_url": _safe_int(row.get("image_with_url")),
            "image_vlm_ok": _safe_int(row.get("image_vlm_ok")),
            "image_vlm_flag_true": _safe_int(row.get("image_vlm_flag_true")),
            "image_content_len_gt_80": _safe_int(row.get("image_content_len_gt_80")),
        }
    return out


def main(argv: Optional[List[str]] = None) -> int:
    _load_env()
    _ensure_utf8_stdio()

    parser = argparse.ArgumentParser(description="Show VLM coverage per document (MongoDB)")
    parser.add_argument("--uri", default=os.getenv("MONGODB_URI", ""), help="Mongo URI (default from env)")
    parser.add_argument("--db", default=os.getenv("MONGODB_DATABASE", "mediarch"), help="Mongo database name")
    parser.add_argument("--category", default=None, help="仅显示指定类别（documents.category / type）")
    parser.add_argument("--only-missing", action="store_true", help="仅显示未完成 VLM 的资料（vlm_ok < with_url）")
    parser.add_argument("--min-ratio", type=float, default=None, help="仅显示 VLM 覆盖率 < min_ratio 的资料（0-1）")
    parser.add_argument("--limit", type=int, default=0, help="最多显示多少条（0=不限制）")
    parser.add_argument("--json", dest="json_path", default=None, help="将结果输出为 JSON 文件")
    args = parser.parse_args(argv)

    uri = (args.uri or "").strip()
    if not uri:
        raise SystemExit("missing MONGODB_URI (set env or pass --uri)")

    from pymongo import MongoClient

    client = MongoClient(uri)
    db = client[str(args.db)]
    docs = db["documents"]
    chunks = db["mediarch_chunks"]

    img_stats = _load_image_stats(chunks)

    q: Dict[str, Any] = {}
    if args.category:
        # 兼容字段：category/type
        q = {"$or": [{"category": str(args.category).strip()}, {"type": str(args.category).strip()}]}

    projection = {"_id": 1, "title": 1, "category": 1, "type": 1, "source_document": 1}
    doc_rows = list(docs.find(q, projection).sort([("_id", 1)]))

    records: List[Dict[str, Any]] = []
    for d in doc_rows:
        doc_id_str = str(d.get("_id"))
        st = img_stats.get(
            doc_id_str,
            {"image_chunks": 0, "image_with_url": 0, "image_vlm_ok": 0, "image_vlm_flag_true": 0, "image_content_len_gt_80": 0},
        )

        title = (d.get("title") or d.get("source_document") or "").strip()
        category = (d.get("category") or d.get("type") or "").strip()
        with_url = _safe_int(st.get("image_with_url"))
        vlm_ok = _safe_int(st.get("image_vlm_ok"))
        ratio = _ratio(vlm_ok, with_url)

        rec = {
            "doc_id": doc_id_str,
            "title": title,
            "category": category,
            **st,
            "vlm_ratio": ratio,
            "status": (
                "no_images" if _safe_int(st.get("image_chunks")) == 0 else ("done" if (with_url > 0 and vlm_ok >= with_url) else "missing")
            ),
        }
        records.append(rec)

    # filter
    filtered: List[Dict[str, Any]] = []
    min_ratio = args.min_ratio
    if min_ratio is not None:
        min_ratio = max(0.0, min(1.0, _safe_float(min_ratio)))

    for rec in records:
        with_url = _safe_int(rec.get("image_with_url"))
        vlm_ok = _safe_int(rec.get("image_vlm_ok"))
        ratio = rec.get("vlm_ratio")

        if args.only_missing and not (with_url > 0 and vlm_ok < with_url):
            continue
        if min_ratio is not None:
            # only keep ratio < min_ratio (and ignore docs with no images)
            if ratio is None:
                continue
            if float(ratio) >= float(min_ratio):
                continue
        filtered.append(rec)

    # sort: missing first, then ratio asc
    def sort_key(r: Dict[str, Any]) -> Tuple[int, float, int]:
        with_url = _safe_int(r.get("image_with_url"))
        vlm_ok = _safe_int(r.get("image_vlm_ok"))
        ratio = r.get("vlm_ratio")
        missing = 1 if (with_url > 0 and vlm_ok < with_url) else 0
        ratio_val = float(ratio) if ratio is not None else 2.0
        return (-missing, ratio_val, -_safe_int(r.get("image_chunks")))

    filtered.sort(key=sort_key)

    if args.limit and args.limit > 0:
        filtered = filtered[: int(args.limit)]

    table = Table(title="VLM Coverage by Document", show_lines=False)
    table.add_column("doc_id", style="cyan", no_wrap=True)
    table.add_column("category", style="magenta")
    table.add_column("title", style="white")
    table.add_column("with_url", justify="right")
    table.add_column("vlm_ok", justify="right")
    table.add_column("long_ok", justify="right")
    table.add_column("flag_true", justify="right")
    table.add_column("ratio", justify="right")
    table.add_column("status", style="yellow")

    for rec in filtered:
        doc_id = str(rec.get("doc_id") or "")
        category = str(rec.get("category") or "")
        title = str(rec.get("title") or "")
        with_url = str(_safe_int(rec.get("image_with_url")))
        vlm_ok = str(_safe_int(rec.get("image_vlm_ok")))
        long_ok = str(_safe_int(rec.get("image_content_len_gt_80")))
        flag_true = str(_safe_int(rec.get("image_vlm_flag_true")))
        ratio = _fmt_ratio(rec.get("vlm_ratio"))
        status = str(rec.get("status") or "")
        table.add_row(doc_id, category, title, with_url, vlm_ok, long_ok, flag_true, ratio, status)

    console.print(table)

    # Suggestions: print a few commands
    missing_cmds = [
        f"python -m backend.cli.vlm_backfill_images --doc-id {rec['doc_id']} --apply --yes"
        for rec in filtered
        if _safe_int(rec.get("image_with_url")) > 0 and _safe_int(rec.get("image_vlm_ok")) < _safe_int(rec.get("image_with_url"))
    ][:5]
    if missing_cmds:
        console.print("\n建议回填命令（示例前5条）：")
        for c in missing_cmds:
            console.print(f"- {c}")

    if args.json_path:
        out_path = Path(str(args.json_path)).expanduser()
        payload = {"count": len(filtered), "items": filtered}
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"\n已输出 JSON: {out_path}")

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
