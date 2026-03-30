#!/usr/bin/env python3
"""Inspect (and optionally clear) KG extraction cache records in MongoDB."""

import argparse
import json
import os
import sys
from datetime import timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from backend.env_loader import load_dotenv
from pymongo import MongoClient

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

load_dotenv()


def _iso(ts) -> Optional[str]:
    if ts is None:
        return None
    if getattr(ts, "tzinfo", None) is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.isoformat()


def _build_existing_chunk_id_set(chunks_collection) -> Set[str]:
    existing: Set[str] = set()
    for doc in chunks_collection.find({}, {"_id": 1, "chunk_id": 1}):
        _id = doc.get("_id")
        if _id is not None:
            existing.add(str(_id))
        cid = doc.get("chunk_id")
        if cid:
            existing.add(str(cid))
    return existing


def _summarize_version(extractions_collection, version: str, existing_chunk_ids: Set[str]) -> Dict[str, Any]:
    q = {"version": version}
    total = extractions_collection.count_documents(q)
    success = extractions_collection.count_documents({**q, "status": "success"})
    failed = extractions_collection.count_documents({**q, "status": "failed"})
    no_status = extractions_collection.count_documents({**q, "status": {"$exists": False}})

    min_ts = None
    max_ts = None
    has_updated_at = 0
    orphan_ids: List[Any] = []
    matched = 0

    cursor = extractions_collection.find(q, {"_id": 1, "chunk_id": 1, "updated_at": 1})
    for doc in cursor:
        ts = doc.get("updated_at")
        if ts is not None:
            has_updated_at += 1
        else:
            try:
                ts = doc.get("_id").generation_time
            except Exception:
                ts = None
        if ts is not None:
            if getattr(ts, "tzinfo", None) is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if min_ts is None or ts < min_ts:
                min_ts = ts
            if max_ts is None or ts > max_ts:
                max_ts = ts

        cid = str(doc.get("chunk_id") or "")
        if cid and cid in existing_chunk_ids:
            matched += 1
        else:
            orphan_ids.append(doc.get("_id"))

    return {
        "version": version,
        "total": total,
        "success": success,
        "failed": failed,
        "no_status": no_status,
        "has_updated_at": has_updated_at,
        "time_min_utc": _iso(min_ts),
        "time_max_utc": _iso(max_ts),
        "matched_chunks": matched,
        "orphan_chunks": len(orphan_ids),
        "orphan_ids": orphan_ids,
    }


def _export_docs(extractions_collection, version: str, out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out_path.open("w", encoding="utf-8") as f:
        for doc in extractions_collection.find({"version": version}):
            if "_id" in doc:
                doc["_id"] = str(doc["_id"])
            if "updated_at" in doc and doc["updated_at"] is not None:
                doc["updated_at"] = _iso(doc["updated_at"])
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
            n += 1
    return n


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect KG extraction cache in MongoDB (kg_extractions).")
    parser.add_argument("--version", help="Inspect only a specific cache version")
    parser.add_argument("--delete-version", action="store_true", help="Delete all cache docs for --version")
    parser.add_argument("--delete-orphans", action="store_true", help="Delete orphan cache docs for --version")
    parser.add_argument("--export", type=str, help="Export cache docs for --version to JSONL before deletion")
    parser.add_argument("--yes", action="store_true", help="Confirm destructive actions")
    args = parser.parse_args()

    mongo_uri = os.getenv("MONGODB_URI")
    if not mongo_uri:
        print("[ERROR] Missing MONGODB_URI")
        return 2

    db_name = os.getenv("MONGODB_DATABASE", "mediarch")
    chunk_collection_name = os.getenv("MONGODB_CHUNK_COLLECTION", "mediarch_chunks")

    client = MongoClient(mongo_uri)
    db = client[db_name]
    chunks = db.get_collection(chunk_collection_name)
    extractions = db.get_collection("kg_extractions")

    existing_chunk_ids = _build_existing_chunk_id_set(chunks)

    versions = [args.version] if args.version else sorted(extractions.distinct("version"))
    if not versions:
        print("[OK] No cache versions found in kg_extractions")
        return 0

    for version in versions:
        summary = _summarize_version(extractions, version, existing_chunk_ids)
        orphan_ids = summary.pop("orphan_ids", [])
        print(json.dumps(summary, ensure_ascii=False, indent=2))

        if not args.version:
            continue

        # Destructive ops only apply to the explicitly targeted version.
        if args.export:
            export_path = Path(args.export)
            exported = _export_docs(extractions, version, export_path)
            print(f"[OK] Exported {exported} docs to {export_path}")

        if (args.delete_version or args.delete_orphans) and not args.yes:
            print("[WARN] Destructive flags provided but not confirmed. Re-run with --yes to apply.")
            continue

        if args.delete_version:
            result = extractions.delete_many({"version": version})
            print(f"[OK] Deleted {result.deleted_count} docs for version={version}")
        elif args.delete_orphans:
            if not orphan_ids:
                print("[OK] No orphan docs to delete.")
            else:
                result = extractions.delete_many({"_id": {"$in": orphan_ids}})
                print(f"[OK] Deleted {result.deleted_count} orphan docs for version={version}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

