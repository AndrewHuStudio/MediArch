# script/reindex_from_ocr.py
# -*- coding: utf-8 -*-
"""从已有 OCR 产物定向重灌 Milvus + MongoDB (不重新 OCR, 不碰 Neo4j)。

设计见 docs/superpowers/specs/2026-06-11-reindex-from-ocr-design.md

核心: 复用 5 个干净积木, 不照抄 pipeline.py 里 OCR 之后那段缠绕代码。
  - MineruClient._read_first_markdown / _read_first_detail : 读现成 OCR 产物
  - ChunkStrategy.chunk_by_hierarchy                       : 切分
  - generate_image_description                            : 图片 VLM 描述
  - EmbeddingGenerator.generate_batch                     : 向量化
  - MilvusWriter / MongoDBWriter                          : 写两库
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from backend.env_loader import load_dotenv
from backend.databases.ingestion.ocr.mineru_client import MineruClient
from backend.databases.ingestion.indexing.chunking import ChunkStrategy

load_dotenv()

# Windows GBK 控制台: 强制 stdout/stderr 用 UTF-8, 避免中文文档名乱码。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

logger = logging.getLogger("reindex_from_ocr")


# OCR 产物根目录 (与 documents.py / mongodb_search.py 同一约定)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OCR_ROOT = Path(
    os.getenv("DATA_PROCESS_OCR_DIR", str(PROJECT_ROOT / "data_process" / "documents_ocr"))
).resolve()


def build_ocr_result(full_dir: Path) -> Dict:
    """读已有 OCR 产物, 拼出与 MineruClient.parse_pdf 兼容的 legacy 结构。

    不调 parse_pdf -> 不重新 OCR。直接复用读取方法读 full/ 目录。

    图片路径的坑: chunking.chunk_by_hierarchy 取 Path(artifacts_dir).parent 作为
    doc_dir, 再找 doc_dir/images/<file>。OCR 图片实际在 full/images/ 下, 因此
    artifacts_dir 必须是 full 目录的子路径, 使其 .parent 正好等于 full。
    """
    full_dir = Path(full_dir).resolve()

    # _read_first_markdown / _read_first_detail 是纯读取方法, 不依赖实例状态,
    # 用 __new__ 绕开 __init__ (避免触发远程 API 客户端初始化)。
    client = MineruClient.__new__(MineruClient)
    markdown = MineruClient._read_first_markdown(client, full_dir) or ""
    detail = MineruClient._read_first_detail(client, full_dir) or []

    artifacts_dir = full_dir / "_reindex"  # .parent == full_dir, 修好图片路径

    return {
        "result": {
            "markdown": markdown,
            "detail": detail,
            "total_page_number": 0,
            "success_count": 0,
        },
        "artifacts_dir": str(artifacts_dir),
    }


def chunk_from_ocr(full_dir: Path, source_document: str, category: str) -> List[Dict]:
    """读 OCR 产物并切分, 返回 chunk 列表 (text + image)。"""
    ocr_result = build_ocr_result(full_dir)
    meta = {
        "type": category,
        "title": source_document,
        "category": category,
        "source_document": source_document,
        "source_category": category,
        "source_directory": category,
        "artifacts_dir": ocr_result["artifacts_dir"],
    }
    return ChunkStrategy().chunk_by_hierarchy(ocr_result, meta)


class DocEntry(NamedTuple):
    """一个已 OCR 文档的定位信息。

    source_document 与正常管线一致, 带 .pdf 后缀 (= OCR 目录名 + '.pdf'),
    这样幂等删旧才能命中旧残片, 检索/citation 也与管线统一。
    """

    category: str
    source_document: str  # 带 .pdf
    full_dir: Path


def discover_documents() -> List[DocEntry]:
    """扫描 OCR_ROOT, 返回所有已 OCR 文档。

    一个文档算 "已 OCR" 当且仅当 <category>/<doc>/full/ 目录存在。
    """
    docs: List[DocEntry] = []
    if not OCR_ROOT.is_dir():
        return docs
    for category_dir in sorted(OCR_ROOT.iterdir()):
        if not category_dir.is_dir():
            continue
        for doc_dir in sorted(category_dir.iterdir()):
            if not doc_dir.is_dir():
                continue
            full_dir = doc_dir / "full"
            if full_dir.is_dir():
                docs.append(
                    DocEntry(
                        category=category_dir.name,
                        source_document=doc_dir.name + ".pdf",
                        full_dir=full_dir,
                    )
                )
    return docs


def describe_images(image_chunks: List[Dict]) -> Tuple[int, int]:
    """对每个 image chunk 调 VLM 生成语义描述, 写回 chunk['content']。

    返回 (成功数, 失败数)。失败的图片保留原占位 content。
    """
    from backend.databases.ingestion.indexing.vision_describer import generate_image_description

    ok, fail = 0, 0
    for ch in image_chunks:
        img_abs = ch.get("image_url_abs")
        if not img_abs or not Path(img_abs).is_file():
            logger.warning("[WARN] 图片不存在, 跳过 VLM: %s", img_abs)
            fail += 1
            continue
        meta = ch.get("metadata") if isinstance(ch.get("metadata"), dict) else {}
        caption = (meta.get("caption") or "").strip()
        try:
            desc = generate_image_description(
                image_path=img_abs,
                ocr_text=caption,
                section=ch.get("section", ""),
                page=(ch.get("page_range") or [0])[0],
            )
            ch["content"] = desc
            # 判定真正生成了描述 (形如 "[图片: xxx] 正文")
            parts = desc.split("] ", 1) if isinstance(desc, str) else []
            if len(parts) == 2 and parts[1].strip():
                ok += 1
            else:
                fail += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("[WARN] VLM 失败 (%s): %s", Path(img_abs).name, exc)
            fail += 1
    return ok, fail


def delete_existing(source_document: str, milvus_writer, mongo_writer) -> Dict[str, int]:
    """按 source_document 删除两库中该文档的旧记录 (幂等可重入)。

    用 source_document 作为稳定键 (而非 doc_id): 旧残片往往是孤儿 chunk ——
    它们的 doc_id 指向早已不存在的 document, 按 doc_id 删根本命不中。因此两库
    都直接按 source_document 删。
    """
    stats = {"milvus": 0, "mongo_chunks": 0, "mongo_docs": 0}

    # --- Milvus: 按 source_document 删 ---
    try:
        safe = source_document.replace("\\", "\\\\").replace('"', '\\"')
        try:
            milvus_writer._ensure_loaded()
        except Exception:  # noqa: BLE001
            pass
        res = milvus_writer.collection.delete(f'source_document == "{safe}"')
        try:
            milvus_writer.collection.flush()
        except Exception:  # noqa: BLE001
            pass
        stats["milvus"] = int(getattr(res, "delete_count", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[WARN] Milvus 删旧失败 (%s): %s", source_document, exc)

    # --- Mongo: 直接按 source_document 删 chunks 与 documents (命中孤儿残片) ---
    try:
        r = mongo_writer.chunks.delete_many({"source_document": source_document})
        stats["mongo_chunks"] = int(r.deleted_count or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[WARN] Mongo 删旧 chunks 失败 (%s): %s", source_document, exc)
    try:
        r = mongo_writer.documents.delete_many({"source_document": source_document})
        stats["mongo_docs"] = int(r.deleted_count or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[WARN] Mongo 删旧 documents 失败 (%s): %s", source_document, exc)

    return stats


def reindex_document(
    category: str,
    doc_name: str,
    full_dir: Path,
    embedder,
    milvus_writer,
    mongo_writer,
    skip_vlm: bool = False,
) -> Dict:
    """重灌单个文档: 读OCR产物 -> 切分 -> 图片VLM -> 删旧 -> 向量化 -> 写两库。"""
    chunks = chunk_from_ocr(full_dir, doc_name, category)
    text_chunks = [c for c in chunks if c.get("content_type") == "text" and (c.get("content") or "").strip()]
    image_chunks = [c for c in chunks if c.get("content_type") == "image"]

    vlm_ok = vlm_fail = 0
    if not skip_vlm and image_chunks:
        vlm_ok, vlm_fail = describe_images(image_chunks)

    # 幂等: 先删旧残片
    del_stats = delete_existing(doc_name, milvus_writer, mongo_writer)

    # 待向量化: 正文 + 有 content 的图片
    to_index = text_chunks + [c for c in image_chunks if (c.get("content") or "").strip()]
    if not to_index:
        logger.warning("[WARN] %s 无可入库 chunk", doc_name)
        return {"doc": doc_name, "text": 0, "image": 0, "status": "empty"}

    # 写 documents 拿 doc_id
    doc_meta = {
        "document_id": f"reindex::{category}::{doc_name}",
        "title": doc_name,
        "source_document": doc_name,
        "type": category,
        "category": category,
        "source_category": category,
        "source_directory": category,
        "ocr_engine": "reuse-ocr",
        "upload_time": datetime.now(timezone.utc),
    }
    mongo_doc_id = mongo_writer.documents.insert_one(doc_meta).inserted_id

    # 向量化 (去重)
    texts = [c["content"] for c in to_index]
    unique = list(dict.fromkeys(texts))
    embs = embedder.generate_batch(unique, batch_size=min(len(unique), 100))
    emb_map = dict(zip(unique, embs))
    for c in to_index:
        c["embedding"] = emb_map.get(c["content"])

    indexed = [c for c in to_index if c.get("embedding") is not None]

    # 写 Milvus
    milvus_writer.insert_vectors(chunks=indexed, doc_id=str(mongo_doc_id))

    # 写 Mongo chunks (不存 embedding)
    from pymongo import InsertOne

    ops = []
    for c in indexed:
        item = dict(c)
        item["doc_id"] = mongo_doc_id
        item.pop("embedding", None)
        ops.append(InsertOne(item))
    ins, skip = mongo_writer._bulk_insert(ops, doc_id=str(mongo_doc_id))

    n_text = sum(1 for c in indexed if c.get("content_type") == "text")
    n_img = sum(1 for c in indexed if c.get("content_type") == "image")
    print(
        f"[OK] {doc_name}: {n_text} text + {n_img} image chunks 入库 "
        f"(删旧 milvus={del_stats['milvus']} mongo_chunks={del_stats['mongo_chunks']}; "
        f"VLM ok={vlm_ok} fail={vlm_fail}; mongo_ins={ins} skip={skip})"
    )
    return {
        "doc": doc_name,
        "text": n_text,
        "image": n_img,
        "vlm_ok": vlm_ok,
        "vlm_fail": vlm_fail,
        "deleted": del_stats,
        "status": "success",
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="从已有 OCR 产物定向重灌 Milvus + MongoDB")
    parser.add_argument("--doc", help="只处理指定文档名 (默认处理全部已 OCR 文档)")
    parser.add_argument("--skip-vlm", action="store_true", help="跳过图片 VLM (只灌正文)")
    parser.add_argument("--dry-run", action="store_true", help="只切分+统计, 不写库")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    all_docs = discover_documents()
    if args.doc:
        # 接受带或不带 .pdf 的名字
        want = {args.doc, args.doc + ".pdf"}
        all_docs = [d for d in all_docs if d.source_document in want]
    if not all_docs:
        print(f"[FAIL] 没有匹配的已 OCR 文档 (OCR_ROOT={OCR_ROOT})")
        return 1

    print(f"[INFO] 待处理 {len(all_docs)} 个文档; dry_run={args.dry_run} skip_vlm={args.skip_vlm}")

    # --- dry-run: 只切分统计, 不连库 ---
    if args.dry_run:
        for entry in all_docs:
            chunks = chunk_from_ocr(entry.full_dir, entry.source_document, entry.category)
            n_text = sum(1 for c in chunks if c.get("content_type") == "text" and (c.get("content") or "").strip())
            n_img = sum(1 for c in chunks if c.get("content_type") == "image")
            n_img_ok = sum(
                1
                for c in chunks
                if c.get("content_type") == "image"
                and c.get("image_url_abs")
                and Path(c["image_url_abs"]).is_file()
            )
            print(f"[DRY] {entry.source_document}: {n_text} text + {n_img} image ({n_img_ok} 图片路径有效)")
        return 0

    # --- 实灌: 初始化两库写入器 ---
    from backend.databases.ingestion.indexing.embedding import EmbeddingGenerator
    from backend.databases.ingestion.indexing.milvus_writer import MilvusWriter
    from backend.databases.ingestion.indexing.mongodb_writer import MongoDBWriter

    embedder = EmbeddingGenerator()
    milvus_writer = MilvusWriter(
        host=os.getenv("MILVUS_HOST", "localhost"), port=os.getenv("MILVUS_PORT", "19530")
    )
    mongo_writer = MongoDBWriter(
        mongo_uri=os.getenv("MONGODB_URI"), database=os.getenv("MONGODB_DATABASE", "mediarch")
    )

    results = []
    for entry in all_docs:
        try:
            results.append(
                reindex_document(
                    entry.category, entry.source_document, entry.full_dir,
                    embedder, milvus_writer, mongo_writer,
                    skip_vlm=args.skip_vlm,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[FAIL] %s 处理失败: %s", entry.source_document, exc)
            results.append({"doc": entry.source_document, "status": "failed", "error": str(exc)})

    ok = sum(1 for r in results if r.get("status") == "success")
    print(f"\n[SUMMARY] 成功 {ok}/{len(results)}")
    for r in results:
        if r.get("status") != "success":
            print(f"  [FAIL] {r['doc']}: {r.get('error', r.get('status'))}")
    try:
        mongo_writer.close()
    except Exception:  # noqa: BLE001
        pass
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
