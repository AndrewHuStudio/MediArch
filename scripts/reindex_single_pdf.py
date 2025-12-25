"""
单文件重新索引（OCR -> 分块 -> VLM -> 向量化 -> MongoDB + Milvus）

你想“局部验证”时用它：先对 1 个 PDF 跑通，再扩大到类别/全量。

用法（项目根目录）：
  # 1) 强制重建（推荐：会删掉该文档旧的 Mongo chunks + Milvus 向量）
  python scripts/reindex_single_pdf.py --category 书籍报告 --pdf "backend/databases/documents/书籍报告/《建筑设计资料集 第6册 医疗》.pdf" --force

  # 2) 快速验证（只跑页段 1-10，不建议用于正式数据）
  python scripts/reindex_single_pdf.py --category 书籍报告 --pdf "backend/databases/documents/书籍报告/《建筑设计资料集 第6册 医疗》.pdf" --pages 1-10

  # 3) 不跑 VLM（只更新 OCR/图片路径/文本分块，节省 API 调用）
  python scripts/reindex_single_pdf.py --category 书籍报告 --pdf "..." --force --no-vlm
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional, Tuple

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")


def _parse_pages(value: Optional[str]) -> Optional[Tuple[int, int]]:
    if not value:
        return None
    raw = value.strip().replace(" ", "")
    if not raw:
        return None
    if "-" not in raw:
        n = int(raw)
        return (n, n)
    a, b = raw.split("-", 1)
    s, e = int(a), int(b)
    if s <= 0 or e < s:
        raise ValueError(f"无效页段: {value}")
    return (s, e)


def _post_audit_mongo(mongo_doc_id: str) -> None:
    try:
        from pymongo import MongoClient
        from bson import ObjectId

        mongo_uri = os.getenv("MONGODB_URI")
        db_name = os.getenv("MONGODB_DATABASE", "mediarch")
        if not mongo_uri:
            print("[WARN] 未配置 MONGODB_URI，跳过 Mongo 审计")
            return

        client = MongoClient(mongo_uri)
        db = client[db_name]
        chunks = db["mediarch_chunks"]

        doc_id_values = []
        doc_id_str = (mongo_doc_id or "").strip()
        if doc_id_str:
            doc_id_values.append(doc_id_str)
            try:
                doc_id_values.append(ObjectId(doc_id_str))
            except Exception:
                pass
        if not doc_id_values:
            print("[WARN] mongo_doc_id 为空，跳过 Mongo 审计")
            return

        q = {"doc_id": {"$in": doc_id_values}}
        total = chunks.count_documents(q)
        img_total = chunks.count_documents({**q, "content_type": "image"})
        img_with_url = chunks.count_documents({**q, "content_type": "image", "image_url": {"$nin": [None, ""]}})

        # VLM 成功：以 metadata.vlm_processed 为准；同时统计 content 长度
        img_vlm_ok = chunks.count_documents({**q, "content_type": "image", "metadata.vlm_processed": True})
        img_long = chunks.count_documents(
            {
                **q,
                "content_type": "image",
                "$expr": {"$gt": [{"$strLenCP": {"$ifNull": ["$content", ""]}}, 80]},
            }
        )

        print("\n" + "=" * 80)
        print("MongoDB 索引后检查")
        print("=" * 80)
        print(f"doc_id: {mongo_doc_id}")
        print(f"chunks 总数: {total}")
        print(f"image chunks: {img_total} | 有 image_url: {img_with_url}")
        print(f"image VLM_ok(metadata.vlm_processed=true): {img_vlm_ok}")
        print(f"image content>80 字符: {img_long}")

        # 优先展示“VLM 真正成功”的图片样例
        sample = chunks.find_one({**q, "content_type": "image", "metadata.vlm_processed": True, "image_url": {"$nin": [None, ""]}})
        if not sample:
            sample = chunks.find_one({**q, "content_type": "image", "image_url": {"$nin": [None, ""]}})
        if sample:
            content = (sample.get("content") or "")[:240]
            meta = sample.get("metadata") or {}
            caption = meta.get("caption") if isinstance(meta, dict) else None
            vlm_ok = meta.get("vlm_processed") if isinstance(meta, dict) else None
            print("\n[样例图片 chunk]")
            print(f"- chunk_id: {sample.get('chunk_id')}")
            print(f"- page_range: {sample.get('page_range')}")
            print(f"- section: {sample.get('section')}")
            print(f"- caption: {caption}")
            print(f"- vlm_processed: {vlm_ok}")
            print(f"- image_url: {sample.get('image_url')}")
            print(f"- content(前240字): {content}")

        client.close()
    except Exception as exc:
        print(f"[WARN] Mongo 审计失败: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reindex a single PDF into MongoDB + Milvus")
    parser.add_argument("--pdf", required=True, help="PDF 路径（建议用绝对或项目内相对路径）")
    parser.add_argument("--category", required=True, help="类别（标准规范/参考论文/书籍报告/政策文件）")
    parser.add_argument("--engine", default="mineru", choices=["mineru", "marker"], help="OCR 引擎")
    parser.add_argument("--force", action="store_true", help="强制重新索引（删除旧 Mongo + Milvus 数据）")
    parser.add_argument("--pages", default=None, help="只跑页段（如 1-10），仅用于快速验证")
    parser.add_argument("--no-vlm", action="store_true", help="禁用 VLM（VLM_ENABLED=0）")
    parser.add_argument("--vlm-max-images", type=int, default=0, help="限制每个文档最多处理多少张图片（VLM_MAX_IMAGES_PER_DOC）")
    parser.add_argument("--vlm-image-pages", type=str, default=None, help="仅对指定页段的图片做 VLM（VLM_IMAGE_PAGE_RANGE，例如 120-160）")
    parser.add_argument("--keep-mineru-outputs", action="store_true", help="保留 mineru_outputs（默认会清理）")
    args = parser.parse_args()

    pdf_path = Path(args.pdf).expanduser()
    if not pdf_path.is_absolute():
        pdf_path = (PROJECT_ROOT / pdf_path).resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF 不存在: {pdf_path}")

    if args.force:
        os.environ["FORCE_REINGEST"] = "1"
    if args.no_vlm:
        os.environ["VLM_ENABLED"] = "0"
    if args.vlm_max_images and args.vlm_max_images > 0:
        os.environ["VLM_MAX_IMAGES_PER_DOC"] = str(int(args.vlm_max_images))
    if args.vlm_image_pages:
        os.environ["VLM_IMAGE_PAGE_RANGE"] = str(args.vlm_image_pages).strip()
    if args.keep_mineru_outputs:
        os.environ["KEEP_MINERU_OUTPUTS"] = "1"

    page_range = _parse_pages(args.pages)

    from backend.databases.ingestion.indexing.pipeline import DocumentIngestionPipeline

    pipe = DocumentIngestionPipeline(engine=args.engine)
    try:
        result = pipe.process_document(
            pdf_path=str(pdf_path),
            category=args.category,
            page_range=page_range,
        )
    finally:
        try:
            pipe.close()
        except Exception:
            pass

    print("\n" + "=" * 80)
    print("索引返回结果")
    print("=" * 80)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result.get("status") == "success" and result.get("mongo_doc_id"):
        # pipeline 返回的是字符串形式的 ObjectId
        _post_audit_mongo(str(result["mongo_doc_id"]))


if __name__ == "__main__":
    main()
