"""
迁移/修复 MongoDB chunks 的 positions 坐标（CLI）

背景：
- 旧数据里 bbox 使用固定 A4 (595x842) 做归一化，导致 positions/position 出现 >1 的越界值；
- 部分 image chunk 只有 legacy 字段 position，没有 positions，前端无法画红框；

本脚本做的事（不重跑 OCR / 不重跑 VLM）：
1) 计算该文档在 Mongo 里 bbox 的缩放因子 scale_x/scale_y（默认用全量 bbox 的最大值，带简单抗离群保护）
2) 将 text/image chunks 的 position/positions 重新缩放到 [0,1]，并 clamp + 保序
3) 为 image chunks 补齐 positions=[{page,bbox:[...]}]

示例：
  python -m backend.cli.migrate_chunk_positions --doc-id 69494a2b818c5afeb13ce2ad --apply
  python -m backend.cli.migrate_chunk_positions --doc-id 69494a2b818c5afeb13ce2ad --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_env() -> None:
    try:
        from backend.env_loader import load_dotenv

        env_path = PROJECT_ROOT / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except Exception:
        pass


def _ensure_utf8_stdio() -> None:
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
    uniq: List[Any] = []
    seen: set[Tuple[str, str]] = set()
    for item in out:
        key = (type(item).__name__, str(item))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    return uniq


def _to_float(value: Any) -> Optional[float]:
    try:
        v = float(value)
        if v != v:  # NaN
            return None
        return v
    except Exception:
        return None


def _quantile(sorted_values: List[float], q: float) -> Optional[float]:
    if not sorted_values:
        return None
    if q <= 0:
        return sorted_values[0]
    if q >= 1:
        return sorted_values[-1]
    idx = int((len(sorted_values) - 1) * q)
    return sorted_values[idx]


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _fix_order(x0: float, y0: float, x1: float, y1: float) -> Tuple[float, float, float, float]:
    if x0 > x1:
        x0, x1 = x1, x0
    if y0 > y1:
        y0, y1 = y1, y0
    return x0, y0, x1, y1


def _round4(v: float) -> float:
    return round(v, 4)


@dataclass(frozen=True)
class Scale:
    x: float
    y: float
    x_source: str
    y_source: str


def _compute_scale(
    cursor: Iterable[Dict[str, Any]],
    quantile_q: float,
    outlier_ratio: float,
) -> Scale:
    x1_values: List[float] = []
    y1_values: List[float] = []

    for doc in cursor:
        # legacy single box
        pos = doc.get("position")
        if isinstance(pos, list) and len(pos) >= 5:
            x1 = _to_float(pos[3])
            y1 = _to_float(pos[4])
            if x1 is not None:
                x1_values.append(x1)
            if y1 is not None:
                y1_values.append(y1)

        # multi-box
        positions = doc.get("positions")
        if isinstance(positions, list):
            for p in positions:
                if not isinstance(p, dict):
                    continue
                bbox = p.get("bbox")
                if not (isinstance(bbox, list) and len(bbox) == 4):
                    continue
                x1 = _to_float(bbox[2])
                y1 = _to_float(bbox[3])
                if x1 is not None:
                    x1_values.append(x1)
                if y1 is not None:
                    y1_values.append(y1)

    x1_values.sort()
    y1_values.sort()

    x_max = x1_values[-1] if x1_values else 1.0
    y_max = y1_values[-1] if y1_values else 1.0
    x_q = _quantile(x1_values, quantile_q) if x1_values else 1.0
    y_q = _quantile(y1_values, quantile_q) if y1_values else 1.0

    # 抗离群：若 max 比高分位大太多，用分位；否则用 max（确保所有值缩放后 <=1）
    if x_q is None:
        x_q = x_max
    if y_q is None:
        y_q = y_max

    if x_q > 0 and x_max > x_q * outlier_ratio:
        x_scale = max(1.0, float(x_q))
        x_source = f"q{int(quantile_q*1000)/10:g}"
    else:
        x_scale = max(1.0, float(x_max))
        x_source = "max"

    if y_q > 0 and y_max > y_q * outlier_ratio:
        y_scale = max(1.0, float(y_q))
        y_source = f"q{int(quantile_q*1000)/10:g}"
    else:
        y_scale = max(1.0, float(y_max))
        y_source = "max"

    return Scale(x=x_scale, y=y_scale, x_source=x_source, y_source=y_source)


def _scale_bbox(bbox: List[Any], scale: Scale) -> Optional[List[float]]:
    if not (isinstance(bbox, list) and len(bbox) == 4):
        return None
    x0 = _to_float(bbox[0])
    y0 = _to_float(bbox[1])
    x1 = _to_float(bbox[2])
    y1 = _to_float(bbox[3])
    if None in (x0, y0, x1, y1):
        return None

    x0 = _clamp01(x0 / scale.x)
    x1 = _clamp01(x1 / scale.x)
    y0 = _clamp01(y0 / scale.y)
    y1 = _clamp01(y1 / scale.y)
    x0, y0, x1, y1 = _fix_order(x0, y0, x1, y1)
    return [_round4(x0), _round4(y0), _round4(x1), _round4(y1)]


def _scale_position_field(pos: List[Any], scale: Scale) -> Optional[List[Any]]:
    if not (isinstance(pos, list) and len(pos) >= 5):
        return None
    page = pos[0]
    bbox = [pos[1], pos[2], pos[3], pos[4]]
    scaled = _scale_bbox(bbox, scale)
    if scaled is None:
        return None
    return [page, scaled[0], scaled[1], scaled[2], scaled[3]]


def main(argv: Optional[List[str]] = None) -> int:
    _load_env()
    _ensure_utf8_stdio()

    parser = argparse.ArgumentParser(description="Migrate/normalize chunk positions for one Mongo document")
    parser.add_argument("--doc-id", default=None, help="Mongo documents/_id 或 documents.document_id")
    parser.add_argument("--all", action="store_true", help="处理所有 doc_id（来自 mediarch_chunks 聚合）")
    parser.add_argument("--db", default=os.getenv("MONGODB_DATABASE", "mediarch"), help="Mongo database name")
    parser.add_argument("--uri", default=os.getenv("MONGODB_URI", ""), help="Mongo URI (default from env)")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要更新的数量，不写入 Mongo")
    parser.add_argument("--apply", action="store_true", help="写入 Mongo（与 --dry-run 二选一）")
    parser.add_argument("--quantile", type=float, default=0.999, help="抗离群分位（默认 0.999）")
    parser.add_argument("--outlier-ratio", type=float, default=1.5, help="max 超过分位多少倍视为离群（默认 1.5）")
    parser.add_argument("--batch-size", type=int, default=500, help="bulk_write 批大小（默认 500）")
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 个 doc（调试用，0 表示不限制）")
    parser.add_argument("--only-needed", action="store_true", help="仅处理需要修复的 doc（scale>1 或图片缺 positions）")
    args = parser.parse_args(argv)

    if bool(args.apply) == bool(args.dry_run):
        raise SystemExit("必须二选一：--apply 或 --dry-run")

    uri = (args.uri or "").strip()
    if not uri:
        raise SystemExit("missing MONGODB_URI (set env or pass --uri)")

    if not args.all:
        if not args.doc_id:
            raise SystemExit("必须提供 --doc-id 或 --all")
        doc_id_values = _as_object_id_candidates(str(args.doc_id))
        if not doc_id_values:
            raise SystemExit("invalid --doc-id")
    else:
        doc_id_values = []

    from pymongo import MongoClient, UpdateOne

    client = MongoClient(uri)
    db = client[str(args.db)]
    chunks = db["mediarch_chunks"]

    # 只投影必要字段
    projection = {"_id": 1, "content_type": 1, "position": 1, "positions": 1}

    def migrate_one(doc_id_candidates: List[Any]) -> Dict[str, int]:
        base_q: Dict[str, Any] = {"doc_id": {"$in": doc_id_candidates}}

        # 1) 计算 scale
        cursor_for_scale = chunks.find(base_q, projection)
        scale = _compute_scale(cursor_for_scale, quantile_q=float(args.quantile), outlier_ratio=float(args.outlier_ratio))
        sys.stdout.write(f"doc_id={str(doc_id_candidates[0])} scale_x={scale.x:.6g} ({scale.x_source}) scale_y={scale.y:.6g} ({scale.y_source})\n")

        # 2) 生成更新
        ops: List[Any] = []
        scanned = 0
        will_update = 0
        updated_position = 0
        updated_positions = 0
        backfilled_image_positions = 0

        cursor = chunks.find(base_q, projection)
        for doc in cursor:
            scanned += 1
            chunk_oid = doc.get("_id")
            content_type = doc.get("content_type")

            update_set: Dict[str, Any] = {}

            # position: [page, x0, y0, x1, y1]
            pos = doc.get("position")
            scaled_pos = _scale_position_field(pos, scale) if isinstance(pos, list) else None
            if scaled_pos is not None and scaled_pos != pos:
                update_set["position"] = scaled_pos
                updated_position += 1

            # positions: [{page,bbox:[...]}]
            positions = doc.get("positions")
            if isinstance(positions, list) and positions:
                new_positions: List[Dict[str, Any]] = []
                changed = False
                for p in positions:
                    if not isinstance(p, dict):
                        new_positions.append(p)
                        continue
                    bbox = p.get("bbox")
                    scaled_bbox = _scale_bbox(bbox, scale)
                    if scaled_bbox is None:
                        new_positions.append(p)
                        continue
                    if scaled_bbox != bbox:
                        changed = True
                    new_p = dict(p)
                    new_p["bbox"] = scaled_bbox
                    new_positions.append(new_p)
                if changed:
                    update_set["positions"] = new_positions
                    updated_positions += 1

            # backfill positions: 若 positions 缺失/为空，但有 legacy position，则补 positions
            # - image: 让前端能画红框
            # - text: 某些标题/短块历史上只有 position，没有 positions，会导致无法黄底高亮
            if (not positions) and isinstance(scaled_pos, list) and len(scaled_pos) >= 5:
                page = scaled_pos[0]
                bbox = [scaled_pos[1], scaled_pos[2], scaled_pos[3], scaled_pos[4]]
                update_set["positions"] = [{"page": page, "bbox": bbox}]
                if content_type == "image":
                    backfilled_image_positions += 1

            if update_set:
                will_update += 1
                ops.append(UpdateOne({"_id": chunk_oid}, {"$set": update_set}))

                if args.apply and len(ops) >= int(max(1, args.batch_size)):
                    chunks.bulk_write(ops, ordered=False)
                    ops.clear()

        if args.apply and ops:
            chunks.bulk_write(ops, ordered=False)
            ops.clear()

        return {
            "scanned": scanned,
            "will_update": will_update,
            "updated_position": updated_position,
            "updated_positions": updated_positions,
            "backfilled_image_positions": backfilled_image_positions,
        }

    if not args.all:
        stats = migrate_one(doc_id_values)
        sys.stdout.write(
            f"scanned={stats['scanned']} will_update={stats['will_update']} "
            f"updated_position={stats['updated_position']} updated_positions={stats['updated_positions']} "
            f"backfilled_image_positions={stats['backfilled_image_positions']}\n"
        )
    else:
        # 聚合出每个 doc 的 max bbox 与 “图片缺 positions” 情况，用于筛选 only-needed
        pipeline = [
            {
                "$facet": {
                    "position_max": [
                        {"$match": {"position": {"$type": "array"}}},
                        {
                            "$project": {
                                "doc_id": 1,
                                "x1": {"$arrayElemAt": ["$position", 3]},
                                "y1": {"$arrayElemAt": ["$position", 4]},
                            }
                        },
                        {"$group": {"_id": "$doc_id", "max_x1": {"$max": "$x1"}, "max_y1": {"$max": "$y1"}}},
                    ],
                    "positions_max": [
                        {"$match": {"positions": {"$type": "array"}}},
                        {"$unwind": "$positions"},
                        {"$match": {"positions.bbox": {"$type": "array"}}},
                        {
                            "$project": {
                                "doc_id": 1,
                                "x1": {"$arrayElemAt": ["$positions.bbox", 2]},
                                "y1": {"$arrayElemAt": ["$positions.bbox", 3]},
                            }
                        },
                        {"$group": {"_id": "$doc_id", "max_x1": {"$max": "$x1"}, "max_y1": {"$max": "$y1"}}},
                    ],
                    "image_missing_positions": [
                        {
                            "$match": {
                                "content_type": "image",
                                "$or": [{"positions": {"$exists": False}}, {"positions": {"$size": 0}}],
                                "position": {"$type": "array"},
                            }
                        },
                        {"$group": {"_id": "$doc_id", "count": {"$sum": 1}}},
                    ],
                    "text_missing_positions": [
                        {
                            "$match": {
                                "content_type": "text",
                                "$or": [{"positions": {"$exists": False}}, {"positions": {"$size": 0}}],
                                "position": {"$type": "array"},
                            }
                        },
                        {"$group": {"_id": "$doc_id", "count": {"$sum": 1}}},
                    ],
                    "docs": [
                        {"$group": {"_id": "$doc_id", "count": {"$sum": 1}}},
                    ],
                }
            }
        ]

        agg = list(chunks.aggregate(pipeline, allowDiskUse=True))
        if not agg:
            sys.stdout.write("no chunks found.\n")
            client.close()
            return 0
        agg0 = agg[0]

        by_doc: Dict[str, Dict[str, Any]] = {}
        def _key(did: Any) -> str:
            return str(did)

        for row in agg0.get("docs") or []:
            by_doc[_key(row.get("_id"))] = {"doc_id": row.get("_id"), "count": int(row.get("count") or 0)}
        for row in agg0.get("position_max") or []:
            by_doc.setdefault(_key(row.get("_id")), {"doc_id": row.get("_id"), "count": 0}).update(
                {"pos_max_x1": row.get("max_x1"), "pos_max_y1": row.get("max_y1")}
            )
        for row in agg0.get("positions_max") or []:
            by_doc.setdefault(_key(row.get("_id")), {"doc_id": row.get("_id"), "count": 0}).update(
                {"poss_max_x1": row.get("max_x1"), "poss_max_y1": row.get("max_y1")}
            )
        for row in agg0.get("image_missing_positions") or []:
            by_doc.setdefault(_key(row.get("_id")), {"doc_id": row.get("_id"), "count": 0}).update(
                {"img_missing_positions": int(row.get("count") or 0)}
            )
        for row in agg0.get("text_missing_positions") or []:
            by_doc.setdefault(_key(row.get("_id")), {"doc_id": row.get("_id"), "count": 0}).update(
                {"text_missing_positions": int(row.get("count") or 0)}
            )

        doc_rows = list(by_doc.values())
        # 稳定排序：chunk 数多的先处理
        doc_rows.sort(key=lambda r: int(r.get("count") or 0), reverse=True)

        if args.only_needed:
            filtered = []
            for r in doc_rows:
                mx = _to_float(r.get("pos_max_x1")) or 0.0
                my = _to_float(r.get("pos_max_y1")) or 0.0
                px = _to_float(r.get("poss_max_x1")) or 0.0
                py = _to_float(r.get("poss_max_y1")) or 0.0
                img_miss = int(r.get("img_missing_positions") or 0)
                txt_miss = int(r.get("text_missing_positions") or 0)
                need = (max(mx, px) > 1.0) or (max(my, py) > 1.0) or (img_miss > 0) or (txt_miss > 0)
                if need:
                    filtered.append(r)
            doc_rows = filtered

        if args.limit and args.limit > 0:
            doc_rows = doc_rows[: int(args.limit)]

        sys.stdout.write(f"docs_to_process={len(doc_rows)} (only_needed={bool(args.only_needed)})\n")

        totals = {"docs": 0, "chunks_scanned": 0, "chunks_will_update": 0}
        for r in doc_rows:
            did = r.get("doc_id")
            if did is None:
                continue
            stats = migrate_one([did])
            totals["docs"] += 1
            totals["chunks_scanned"] += int(stats["scanned"])
            totals["chunks_will_update"] += int(stats["will_update"])

        sys.stdout.write(
            f"done docs={totals['docs']} chunks_scanned={totals['chunks_scanned']} chunks_will_update={totals['chunks_will_update']}\n"
        )

    if args.dry_run:
        sys.stdout.write("dry-run: no writes performed.\n")
    else:
        sys.stdout.write("apply: writes performed.\n")

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
