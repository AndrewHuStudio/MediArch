# -*- coding: utf-8 -*-
"""
交互式 VLM 管理器（终端 TUI）

你可以在终端里：
1) 一眼看到每份资料的 VLM 覆盖率/质量（高标准/完成/未开始/进行中）
2) 看到图片数量、预计剩余耗时/费用（基于最近 VLM 调用日志估算）
3) 选择资料并直接开始 VLM 回填（不重跑 OCR）

运行：
  python -m backend.cli.vlm_manager

依赖：
  pip install rich pymongo python-dotenv

说明：
- 时间/费用是估算值：优先读取 `VLM_USAGE_LOG_FILE`（JSONL）里最近的真实调用耗时/费用；
  若没有日志，则用环境变量兜底：
    - VLM_ESTIMATE_SEC_PER_IMAGE（默认 25）
    - VLM_ESTIMATE_USD_PER_IMAGE（默认从 VLM_PRICE_PER_CALL_USD 推导；否则为空）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.env_loader import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text


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


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except Exception:
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _fmt_ratio(r: Optional[float]) -> str:
    if r is None:
        return "-"
    return f"{r*100:.1f}%"


def _fmt_money(usd: Optional[float]) -> str:
    if usd is None:
        return "-"
    return f"${usd:.3f}"


def _fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "-"
    s = max(0, int(round(seconds)))
    h = s // 3600
    m = (s % 3600) // 60
    ss = s % 60
    if h > 0:
        return f"{h:d}:{m:02d}:{ss:02d}"
    return f"{m:d}:{ss:02d}"


def _parse_ranges(expr: str) -> List[int]:
    """
    Parse "1,3-5,8" into [1,3,4,5,8].
    """
    out: List[int] = []
    raw = (expr or "").strip()
    if not raw:
        return out
    for part in raw.split(","):
        p = part.strip()
        if not p:
            continue
        if "-" in p:
            a, b = p.split("-", 1)
            try:
                s, e = int(a), int(b)
            except Exception:
                continue
            if s <= 0 or e < s:
                continue
            out.extend(list(range(s, e + 1)))
        else:
            try:
                out.append(int(p))
            except Exception:
                continue
    # de-dup keep order
    seen = set()
    uniq: List[int] = []
    for x in out:
        if x in seen:
            continue
        seen.add(x)
        uniq.append(x)
    return uniq


def _read_vlm_usage_log(path: Path, max_lines: int = 2000) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    items: List[Dict[str, Any]] = []
    try:
        # read tail-ish: for simplicity read all if small; otherwise last N lines
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if max_lines and len(lines) > max_lines:
            lines = lines[-max_lines:]
        for line in lines:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                items.append(obj)
    except Exception:
        return []
    return items


def _estimate_from_log(log_items: List[Dict[str, Any]]) -> Tuple[Optional[float], Optional[float], Dict[str, Any]]:
    """
    Compute avg seconds/cost per billed image call.
    Exclude cached hits and failed calls.
    """
    billed: List[Dict[str, Any]] = []
    for it in log_items:
        if not isinstance(it, dict):
            continue
        if it.get("cached") is True:
            continue
        if it.get("ok") is not True:
            continue
        dur = it.get("duration_s")
        if dur is None:
            continue
        billed.append(it)

    avg_s: Optional[float] = None
    if billed:
        avg_s = sum(_safe_float(x.get("duration_s")) for x in billed) / float(len(billed))

    costs = [x for x in billed if x.get("cost_usd") is not None]
    avg_usd: Optional[float] = None
    if costs:
        avg_usd = sum(_safe_float(x.get("cost_usd")) for x in costs) / float(len(costs))

    meta = {"samples_total": len(log_items), "samples_billed": len(billed), "samples_cost": len(costs)}
    return avg_s, avg_usd, meta


def _ratio(n: int, d: int) -> Optional[float]:
    if d <= 0:
        return None
    return float(n) / float(d)


def _load_image_stats(chunks_coll) -> Dict[str, Dict[str, int]]:
    """
    Aggregate image chunk stats by doc_id (stringified).
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
                # Historical data may have metadata.vlm_processed=true even if VLM wasn't enabled.
                # Treat "vlm_ok" as: content contains a real description after the bracket, e.g. "[图片: ...] xxx".
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
                        {"$gt": [{"$strLenCP": {"$ifNull": ["$content", ""]}}, 80]},
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


def _classify(rec: Dict[str, Any], high_ratio: float, high_long_ratio: float) -> str:
    with_url = _safe_int(rec.get("image_with_url"))
    vlm_ok = _safe_int(rec.get("image_vlm_ok"))
    long_ok = _safe_int(rec.get("image_content_len_gt_80"))
    if with_url <= 0:
        return "NO_IMAGES"
    if vlm_ok <= 0:
        return "NOT_STARTED"
    if vlm_ok < with_url:
        return "IN_PROGRESS"
    # done
    r = _ratio(vlm_ok, with_url) or 0.0
    lr = _ratio(long_ok, with_url) or 0.0
    if r >= high_ratio and lr >= high_long_ratio:
        return "HIGH"
    return "DONE"


def _status_style(status: str) -> str:
    return {
        "HIGH": "green",
        "DONE": "cyan",
        "IN_PROGRESS": "yellow",
        "NOT_STARTED": "red",
        "NO_IMAGES": "dim",
    }.get(status, "white")


def _print_banner(avg_s: Optional[float], avg_usd: Optional[float], meta: Dict[str, Any], log_path: Path) -> None:
    lines: List[str] = []
    lines.append(f"VLM model: {os.getenv('VLM_MODEL') or os.getenv('VLM_MODE') or os.getenv('KG_VISION_MODEL') or 'qwen3-vl-plus'}")
    lines.append(f"Usage log: {log_path}")
    lines.append(f"Avg time / image: {_fmt_duration(avg_s)} (samples={meta.get('samples_billed', 0)})")
    lines.append(f"Avg cost / image: {_fmt_money(avg_usd)} (cost samples={meta.get('samples_cost', 0)})")
    lines.append("Tips: 选择资料分批回填（不重跑 OCR），先从关键页段/关键资料开始。")
    console.print(Panel("\n".join(lines), title="MediArch VLM Manager", border_style="blue"))


def _fetch_records(
    mongo_uri: str,
    mongo_db: str,
    category: Optional[str] = None,
) -> List[Dict[str, Any]]:
    from pymongo import MongoClient

    client = MongoClient(mongo_uri)
    db = client[mongo_db]
    docs = db["documents"]
    chunks = db["mediarch_chunks"]

    img_stats = _load_image_stats(chunks)

    q: Dict[str, Any] = {}
    if category:
        cat = str(category).strip()
        q = {"$or": [{"category": cat}, {"type": cat}]}

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
        cat = (d.get("category") or d.get("type") or "").strip()
        with_url = _safe_int(st.get("image_with_url"))
        vlm_ok = _safe_int(st.get("image_vlm_ok"))
        long_ok = _safe_int(st.get("image_content_len_gt_80"))

        records.append(
            {
                "doc_id": doc_id_str,
                "title": title,
                "category": cat,
                **st,
                "ratio": _ratio(vlm_ok, with_url),
                "long_ratio": _ratio(long_ok, with_url),
            }
        )

    client.close()
    return records


def _render_table(
    records: List[Dict[str, Any]],
    avg_s: Optional[float],
    avg_usd: Optional[float],
    high_ratio: float,
    high_long_ratio: float,
    only: str = "all",
    min_ratio: Optional[float] = None,
) -> Tuple[Table, List[Dict[str, Any]]]:
    only = (only or "all").strip().lower()
    min_ratio = None if min_ratio is None else max(0.0, min(1.0, float(min_ratio)))

    filtered: List[Dict[str, Any]] = []
    for rec in records:
        status = _classify(rec, high_ratio=high_ratio, high_long_ratio=high_long_ratio)
        with_url = _safe_int(rec.get("image_with_url"))
        vlm_ok = _safe_int(rec.get("image_vlm_ok"))
        ratio = rec.get("ratio")
        remaining = max(0, with_url - vlm_ok)

        if only == "missing":
            if not (with_url > 0 and vlm_ok < with_url):
                continue
        elif only == "not_started":
            if not (with_url > 0 and vlm_ok == 0):
                continue
        elif only == "high":
            if status != "HIGH":
                continue

        if min_ratio is not None:
            if ratio is None:
                continue
            if float(ratio) >= float(min_ratio):
                continue

        # derived fields
        rec2 = dict(rec)
        rec2["status"] = status
        rec2["remaining"] = remaining
        rec2["est_time_remain_s"] = (float(remaining) * float(avg_s)) if (avg_s is not None and remaining > 0) else 0.0
        rec2["est_cost_remain_usd"] = (float(remaining) * float(avg_usd)) if (avg_usd is not None and remaining > 0) else None
        rec2["est_time_spent_s"] = (float(vlm_ok) * float(avg_s)) if (avg_s is not None and vlm_ok > 0) else 0.0
        rec2["est_cost_spent_usd"] = (float(vlm_ok) * float(avg_usd)) if (avg_usd is not None and vlm_ok > 0) else None
        filtered.append(rec2)

    # sort: not started -> in progress -> done/high -> no_images, then ratio asc, then remaining desc
    order_map = {"NOT_STARTED": 0, "IN_PROGRESS": 1, "DONE": 2, "HIGH": 3, "NO_IMAGES": 4}

    def s_key(r: Dict[str, Any]) -> Tuple[int, float, int]:
        st = str(r.get("status") or "")
        ratio = r.get("ratio")
        rem = _safe_int(r.get("remaining"))
        return (order_map.get(st, 9), float(ratio) if ratio is not None else 2.0, -rem)

    filtered.sort(key=s_key)

    table = Table(title="VLM Coverage (per document)", show_lines=False)
    table.add_column("#", justify="right", style="dim", no_wrap=True)
    table.add_column("status", no_wrap=True)
    table.add_column("doc_id", style="cyan", no_wrap=True)
    table.add_column("category", style="magenta")
    table.add_column("title")
    table.add_column("img", justify="right")
    table.add_column("vlm_ok", justify="right")
    table.add_column("ratio", justify="right")
    table.add_column("long>80", justify="right")
    table.add_column("remain", justify="right")
    table.add_column("est_remain", justify="right")
    table.add_column("est_cost", justify="right")

    for idx, rec in enumerate(filtered, start=1):
        status = str(rec.get("status") or "")
        style = _status_style(status)
        with_url = _safe_int(rec.get("image_with_url"))
        vlm_ok = _safe_int(rec.get("image_vlm_ok"))
        long_ratio = rec.get("long_ratio")
        remain = _safe_int(rec.get("remaining"))

        status_text = Text(status, style=style)
        table.add_row(
            str(idx),
            status_text,
            str(rec.get("doc_id") or ""),
            str(rec.get("category") or ""),
            str(rec.get("title") or ""),
            str(with_url),
            str(vlm_ok),
            _fmt_ratio(rec.get("ratio")),
            _fmt_ratio(long_ratio),
            str(remain),
            _fmt_duration(_safe_float(rec.get("est_time_remain_s"))),
            _fmt_money(rec.get("est_cost_remain_usd")),
        )

    return table, filtered


def _run_backfill_for_docs(
    doc_ids: List[str],
    page_range: Optional[str],
    max_images_per_doc: int,
    limit: int,
    batch_size: int,
    yes: bool = True,
) -> None:
    from backend.cli import vlm_backfill_images

    for doc_id in doc_ids:
        argv = ["--apply"]
        if yes:
            argv.append("--yes")
        argv += ["--doc-id", str(doc_id)]
        if page_range:
            argv += ["--page-range", str(page_range)]
        if max_images_per_doc and max_images_per_doc > 0:
            argv += ["--max-images-per-doc", str(int(max_images_per_doc))]
        if limit and limit > 0:
            argv += ["--limit", str(int(limit))]
        if batch_size and batch_size > 0:
            argv += ["--batch-size", str(int(batch_size))]

        console.print(Panel(f"Backfill doc_id={doc_id}", border_style="blue"))
        try:
            vlm_backfill_images.main(argv)
        except SystemExit:
            # keep running next docs
            pass


def main(argv: Optional[List[str]] = None) -> int:
    _load_env()
    _ensure_utf8_stdio()

    parser = argparse.ArgumentParser(description="Interactive VLM manager")
    parser.add_argument("--category", default=None, help="仅显示指定类别（documents.category/type）")
    parser.add_argument("--only", default="all", choices=["all", "missing", "not_started", "high"], help="默认视图过滤")
    parser.add_argument("--min-ratio", type=float, default=None, help="仅显示 VLM 覆盖率 < min_ratio 的资料（0-1）")
    parser.add_argument("--high-ratio", type=float, default=0.95, help="高标准：vlm_ok/with_url >= high_ratio")
    parser.add_argument("--high-long-ratio", type=float, default=0.70, help="高标准：long>80/with_url >= high_long_ratio")
    parser.add_argument("--max-log-lines", type=int, default=2000, help="读取 usage log 的最大行数")
    args = parser.parse_args(argv)

    mongo_uri = (os.getenv("MONGODB_URI") or "").strip()
    mongo_db = (os.getenv("MONGODB_DATABASE") or "mediarch").strip()
    if not mongo_uri:
        raise SystemExit("missing MONGODB_URI (set env or .env)")

    log_path = Path(os.getenv("VLM_USAGE_LOG_FILE", "backend/databases/ingestion/vlm_usage.jsonl"))
    log_items = _read_vlm_usage_log((PROJECT_ROOT / log_path).resolve() if not log_path.is_absolute() else log_path, max_lines=int(args.max_log_lines))
    avg_s, avg_usd, meta = _estimate_from_log(log_items)

    # fallbacks
    if avg_s is None:
        avg_s = _safe_float(os.getenv("VLM_ESTIMATE_SEC_PER_IMAGE"), 25.0)
    if avg_usd is None:
        # try per-call price
        per_call = _safe_float(os.getenv("VLM_PRICE_PER_CALL_USD"), 0.0)
        if per_call > 0:
            avg_usd = per_call
        else:
            fallback_usd = os.getenv("VLM_ESTIMATE_USD_PER_IMAGE")
            avg_usd = _safe_float(fallback_usd, 0.0) if (fallback_usd is not None and str(fallback_usd).strip()) else None

    while True:
        console.clear()
        _print_banner(avg_s=avg_s, avg_usd=avg_usd, meta=meta, log_path=log_path)

        records = _fetch_records(mongo_uri=mongo_uri, mongo_db=mongo_db, category=args.category)
        table, filtered = _render_table(
            records=records,
            avg_s=avg_s,
            avg_usd=avg_usd,
            high_ratio=max(0.0, min(1.0, float(args.high_ratio))),
            high_long_ratio=max(0.0, min(1.0, float(args.high_long_ratio))),
            only=args.only,
            min_ratio=args.min_ratio,
        )

        console.print(table)

        # summary
        total_with_url = sum(_safe_int(r.get("image_with_url")) for r in filtered)
        total_ok = sum(_safe_int(r.get("image_vlm_ok")) for r in filtered)
        total_rem = sum(_safe_int(r.get("remaining")) for r in filtered)
        total_time = float(total_rem) * float(avg_s) if avg_s is not None else 0.0
        total_cost = (float(total_rem) * float(avg_usd)) if (avg_usd is not None) else None
        console.print(
            Panel(
                f"Docs: {len(filtered)} | images(with_url): {total_with_url} | vlm_ok: {total_ok} | remain: {total_rem} | est remain: {_fmt_duration(total_time)} | est cost: {_fmt_money(total_cost)}",
                border_style="dim",
            )
        )

        action = Prompt.ask("\n[cyan]动作[/cyan]: (s)选择回填 | (m)全选缺失 | (a)全选待做 | (r)刷新 | (q)退出", default="q").strip().lower()
        if action in {"q", "quit", "exit"}:
            return 0
        if action in {"r", "refresh"}:
            continue

        selected: List[int] = []
        if action in {"m", "missing"}:
            for i, r in enumerate(filtered, start=1):
                with_url = _safe_int(r.get("image_with_url"))
                ok = _safe_int(r.get("image_vlm_ok"))
                if with_url > 0 and ok < with_url:
                    selected.append(i)
        elif action in {"a", "all-pending"}:
            # 选择所有 NOT_STARTED 的资料（完全未处理）
            for i, r in enumerate(filtered, start=1):
                status = str(r.get("status") or "")
                if status == "NOT_STARTED":
                    selected.append(i)
        elif action in {"s", "select"}:
            raw = Prompt.ask("[yellow]输入序号[/yellow]（如 1,3-5 或 1-15），留空=取消", default="").strip()
            if not raw:
                continue
            selected = _parse_ranges(raw)
        else:
            continue

        # map to doc_ids
        doc_ids: List[str] = []
        for i in selected:
            if 1 <= i <= len(filtered):
                doc_ids.append(str(filtered[i - 1].get("doc_id")))
        doc_ids = [d for d in doc_ids if d]
        if not doc_ids:
            console.print("[yellow]未选择任何资料[/yellow]")
            Prompt.ask("回车继续", default="")
            continue

        # options
        console.print("\n[cyan]== 回填参数设置 ==[/cyan]")
        page_range = Prompt.ask("[dim]限制页段[/dim]（如 145-160），留空=全图", default="").strip() or None
        max_per_doc = _safe_int(Prompt.ask("[dim]每份资料最多处理多少张图[/dim]（0=不限）", default="0"))
        limit_total = _safe_int(Prompt.ask("[dim]总处理上限[/dim]（0=不限）", default="0"))
        batch_size = _safe_int(Prompt.ask("[dim]批次大小[/dim]（同步 Milvus/Embedding，建议32-50）", default="32"))

        # estimate for selected docs
        remaining_total = 0
        for r in filtered:
            if str(r.get("doc_id")) in set(doc_ids):
                remaining_total += _safe_int(r.get("remaining"))
        est_time = float(remaining_total) * float(avg_s) if avg_s is not None else 0.0
        est_cost = (float(remaining_total) * float(avg_usd)) if avg_usd is not None else None

        console.print(
            Panel(
                f"[bold]回填方案预览[/bold]\n"
                f"资料数: {len(doc_ids)} | 预计处理图片: {remaining_total} | 耗时: {_fmt_duration(est_time)} | 费用: {_fmt_money(est_cost)}\n"
                f"doc_ids: {', '.join(doc_ids[:4])}{' ...' if len(doc_ids) > 4 else ''}",
                border_style="yellow",
            )
        )

        confirm = Prompt.ask("[bold yellow]确认开始？[/bold yellow] (yes/no)", default="no").strip().lower()
        if confirm not in {"y", "yes"}:
            continue

        _run_backfill_for_docs(
            doc_ids=doc_ids,
            page_range=page_range,
            max_images_per_doc=max_per_doc,
            limit=limit_total,
            batch_size=batch_size,
            yes=True,
        )
        Prompt.ask("\n已执行完毕，回车刷新", default="")


if __name__ == "__main__":
    raise SystemExit(main())
