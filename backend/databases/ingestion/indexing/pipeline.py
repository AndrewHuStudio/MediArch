# backend/databases/ingestion/indexing/pipeline.py
# -*- coding: utf-8 -*-

"""
文档导入 Pipeline（优化版 v2 - 2025-01-22）

核心改进：
- ✨ 集成 VLM：图片自动生成语义描述（qwen3-vl-plus）
- ✨ 增大 chunk_max：1200字符（保持条款完整）
- ✨ 归一化坐标：position 转为 [0,1] 比例坐标
- 结构化日志 logging 替代 print；step 计时器
- 幂等：documents.document_id 唯一；FORCE_REINGEST 可控重跑
- 前置校验 _preflight：确保唯一索引与常用索引存在；Milvus 预加载；Mongo 连接预热
- 全部 UTC 时间；更合理的 OCR 置信度字段（优先 confidence/score）
- 图片并行拷贝（同盘硬链接优先）+ I/O 限流（MAX_IMG_WORKERS, IMG_IO_SLEEP）
- Markdown 仅替换图片链接 ![]()
- 向量化流式分批（去重 + 动态批次），每批立刻写 Milvus 与 Mongo chunks
- 先写 documents（拿 _id），再逐批写 chunks（Mongo 不存 embedding）与向量（Milvus）
- 返回详细统计（嵌入条数、插入/跳过条数、时长等）
"""

import os
import uuid
import json
import shutil
import logging
from typing import Dict, Optional, Tuple, List
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager
import re as _re
import time

from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed

# 依赖：请确保下面模块路径与项目结构一致
from backend.databases.ingestion.ocr.mineru_client import MineruClient
from backend.databases.ingestion.ocr.ocr_progress_tracker import OCRProgressTracker
from backend.databases.ingestion.indexing.chunking import ChunkStrategy
from backend.databases.ingestion.indexing.embedding import EmbeddingGenerator
from backend.databases.ingestion.indexing.mongodb_writer import MongoDBWriter
from backend.databases.ingestion.indexing.milvus_writer import MilvusWriter
from backend.databases.ingestion.indexing.vision_describer import generate_image_description  # ✨ VLM

load_dotenv()
logger = logging.getLogger(__name__)
# 在应用入口配置一次：logging.basicConfig(level=logging.INFO)
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO)


@contextmanager
def step_timer(step: str, ctx: dict):
    t0 = datetime.now(timezone.utc)
    logger.info("step_start %s %s", step, ctx)
    try:
        yield
        t1 = datetime.now(timezone.utc)
        logger.info("step_ok %s duration=%.3fs %s", step, (t1 - t0).total_seconds(), ctx)
    except Exception as e:
        t1 = datetime.now(timezone.utc)
        logger.exception("step_fail %s duration=%.3fs %s error=%s", step, (t1 - t0).total_seconds(), ctx, e)
        raise


class DocumentIngestionPipeline:
    """文档导入Pipeline（OCR -> 分块 -> 向量化 -> Mongo/Milvus）"""

    def __init__(self, engine: Optional[str] = None):
        logger.info("Initializing Document Ingestion Pipeline...")
        self.engine = (engine or os.getenv("OCR_ENGINE") or "textin").strip().lower()

        self.engine == "mineru"
        self.ocr_client = MineruClient(
            project_root=os.getenv("MINERU_PROJECT_ROOT"),
            mineru_exe=os.getenv("MINERU_EXE", "mineru"),
            python_exe=os.getenv("MINERU_PYTHON_EXE", "python"),
            backend=os.getenv("MINERU_BACKEND", "pipeline"),
            use_cuda=(os.getenv("MINERU_USE_CUDA", "0").lower() in {"1", "true"}),
        )
        
        self.chunk_strategy = ChunkStrategy(
            max_chunk_size=int(os.getenv("CHUNK_MAX", "1200")),  # ✨ 提升到1200
            min_chunk_size=int(os.getenv("CHUNK_MIN", "100")),
            merge_small_chunks=True,
            chunk_overlap=int(os.getenv("CHUNK_OVERLAP", "100")),
            normalize_positions=True,  # ✨ 启用坐标归一化
        )
        
        self.embedding_generator = EmbeddingGenerator()
        
        self.mongodb_writer = MongoDBWriter(
            mongo_uri=os.getenv("MONGODB_URI"),
            database=os.getenv("MONGODB_DATABASE", "mediarch")
        )
        
        self.milvus_writer = MilvusWriter(
            host=os.getenv("MILVUS_HOST", "localhost"),
            port=os.getenv("MILVUS_PORT", "19530")
        )
        
        self.tracker = OCRProgressTracker()
        
        # 前置校验与预热（确保索引/幂等不退化）
        try:
            self._preflight()
        except Exception:
            logger.exception("Pipeline preflight failed (continuing anyway)")

        logger.info("[OK] Pipeline initialized")

    # ----------------- 前置校验与预热 -----------------
    def _preflight(self):
        """确保幂等唯一索引/常用索引存在，并进行轻量暖库"""
        from pymongo import ASCENDING

        # 1) documents.document_id 唯一索引（幂等基石）
        try:
            idx_names = {ix["name"] for ix in self.mongodb_writer.documents.list_indexes()}
            if "document_id_unique" not in idx_names:
                logger.warning("documents.document_id unique index missing; creating...")
                self.mongodb_writer.documents.create_index(
                    [("document_id", ASCENDING)],
                    name="document_id_unique",
                    unique=True, sparse=True, background=True,
                )
                logger.info("created unique index: documents.document_id")
        except Exception as e:
            logger.exception("preflight: ensure documents.document_id unique index failed: %s", e)

        # 2) chunks 常用索引补齐
        try:
            ch_idx = {ix["name"] for ix in self.mongodb_writer.chunks.list_indexes()}
            need = []
            if "chunk_id_unique" not in ch_idx:
                need.append(("chunk_id_unique", [("chunk_id", ASCENDING)], dict(unique=True)))
            if "doc_seq_idx" not in ch_idx:
                need.append(("doc_seq_idx", [("doc_id", ASCENDING), ("sequence", ASCENDING)], {}))
            if "image_url_sparse" not in ch_idx:
                need.append(("image_url_sparse", [("image_url", ASCENDING)], dict(sparse=True)))
            for name, spec, kw in need:
                self.mongodb_writer.chunks.create_index(spec, name=name, background=True, **kw)
        except Exception:
            pass

        # 3) 轻量预热（Mongo 连接、Milvus collection）
        try:
            _ = self.mongodb_writer.documents.find_one({}, projection={"_id": 1})
        except Exception:
            pass
        try:
            if getattr(self.milvus_writer, "_loaded", False) is False:
                self.milvus_writer.collection.load()
                self.milvus_writer._loaded = True
        except Exception:
            pass

    # ----------------- 工具函数 -----------------
    @staticmethod
    def _probe_total_pages(pdf_path: str) -> int:
        try:
            try:
                from pypdf import PdfReader
                return len(PdfReader(pdf_path).pages)
            except Exception:
                pass
            try:
                from PyPDF2 import PdfReader
                return len(PdfReader(pdf_path).pages)
            except Exception:
                pass
            try:
                import fitz
                return len(fitz.open(pdf_path))
            except Exception:
                pass
        except Exception:
            pass
        return -1

    @staticmethod
    def _upsert_markdown_segment(md_path: str, start_page: int, end_page: int, content: str) -> None:
        import re
        marker_pattern = re.compile(r"<!-- pages (\d+)-(\d+) -->")
        segments: Dict[Tuple[int, int], str] = {}
        order: List[Tuple[int, int]] = []

        if os.path.exists(md_path):
            existing = Path(md_path).read_text(encoding="utf-8")
            matches = list(marker_pattern.finditer(existing))
            for idx, match in enumerate(matches):
                seg_start = match.end()
                seg_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(existing)
                key = (int(match.group(1)), int(match.group(2)))
                segments[key] = existing[seg_start:seg_end]
                order.append(key)

        body = (content or "").strip()
        body = ("\n" + body + "\n") if body else "\n"
        from datetime import datetime as _dt
        run_tag = f"<!-- ingest-run {_dt.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} pages {start_page}-{end_page} -->\n"
        key = (int(start_page), int(end_page))
        segments[key] = run_tag + body
        if key not in order:
            order.append(key)

        order = sorted(set(order), key=lambda x: x[0])
        output_parts = []
        for seg_key in order:
            seg_body = (segments.get(seg_key, "") or "").rstrip() + "\n"
            part = f"<!-- pages {seg_key[0]}-{seg_key[1]} -->{seg_body}"
            output_parts.append(part)

        final_text = "\n\n".join(output_parts)
        Path(md_path).write_text(final_text, encoding="utf-8")

    @staticmethod
    def _find_artifact_images(artifacts_dir: str) -> List[str]:
        exts = {".png", ".jpg", ".jpeg", ".webp"}
        imgs: List[str] = []
        try:
            base = Path(artifacts_dir)
            for p in base.rglob("*"):
                if p.is_file() and p.suffix.lower() in exts:
                    imgs.append(str(p.resolve()))
        except Exception:
            pass
        return imgs

    @staticmethod
    def _copy_one(src: str, dst_dir: Path) -> Optional[tuple]:
        sp = Path(src)
        out = dst_dir / sp.name
        try:
            os.link(str(sp), str(out))  # 同盘硬链接
        except Exception:
            try:
                shutil.copy2(str(sp), str(out))
            except Exception:
                return None
        return (str(sp.resolve()), f"images/{sp.name}")

    @staticmethod
    def _copy_images_fast(images: List[str], images_dir: str) -> Dict[str, str]:
        """
        并行拷图，受 MAX_IMG_WORKERS 控制并行度（默认 8）；慢盘上建议调小，比如 2~4。
        每 64 张后小睡 IMG_IO_SLEEP 秒（默认 0.05），减少 IOPS 抖动。
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        max_workers = int(os.getenv("MAX_IMG_WORKERS", "8"))
        max_workers = max(1, min(max_workers, 32))
        batch_size = 64
        small_sleep = float(os.getenv("IMG_IO_SLEEP", "0.05"))  # 秒

        mapping: Dict[str, str] = {}
        dst_dir = Path(images_dir)
        dst_dir.mkdir(parents=True, exist_ok=True)

        if not images:
            return mapping

        for s in range(0, len(images), batch_size):
            part = images[s:s + batch_size]
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futs = [ex.submit(DocumentIngestionPipeline._copy_one, src, dst_dir) for src in part]
                for fu in as_completed(futs):
                    r = fu.result()
                    if r:
                        mapping[r[0]] = r[1]
            if s + batch_size < len(images) and small_sleep > 0:
                time.sleep(small_sleep)

        return mapping

    @staticmethod
    def _rmtree_force(path: str) -> None:
        """在 Windows 上强制删除目录（去掉只读标志并重试）。"""
        def _onerror(func, p, exc_info):
            try:
                os.chmod(p, 0o700)
                func(p)
            except Exception:
                pass
        try:
            shutil.rmtree(path, onerror=_onerror)
        except Exception:
            # 再尝试一次（偶发占用）
            try:
                shutil.rmtree(path, ignore_errors=True)
            except Exception:
                pass

    @staticmethod
    def _rewrite_md_images(md_text: str, path_map: Dict[str, str]) -> str:
        """只替换 ![]() 图片链接"""
        if not md_text or not path_map:
            return md_text or ""
        base_map = {Path(k).name: v for k, v in path_map.items()}
        pat = _re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
        def repl(m):
            url = m.group(1)
            new = path_map.get(url) or base_map.get(Path(url).name)
            return m.group(0).replace(f"({url})", f"({new})") if new else m.group(0)
        return pat.sub(repl, md_text)

    @staticmethod
    def _generate_visual_pdfs(artifacts_dir: str, input_pdf: str, doc_dir: str, page_range: Optional[Tuple[int, int]]) -> tuple:
        ok_layout = False
        ok_span = False
        try:
            base = Path(artifacts_dir)
            # 直接拷贝 MinerU 生成的可视化 PDF（若存在）
            layout = None
            for cand in (list(base.rglob("_layout.pdf")) + list(base.rglob("*_layout.pdf"))):
                layout = cand; break
            if layout:
                try:
                    shutil.copy2(str(layout), str(Path(doc_dir) / "_layout.pdf"))
                    ok_layout = True
                except Exception:
                    pass
            span = None
            for cand in (list(base.rglob("_span.pdf")) + list(base.rglob("*_span.pdf"))):
                span = cand; break
            if span:
                try:
                    shutil.copy2(str(span), str(Path(doc_dir) / "_span.pdf"))
                    ok_span = True
                except Exception:
                    pass
        except Exception:
            pass
        return ok_layout, ok_span

    # ----------------- 主流程 -----------------
    def process_document(
        self,
        pdf_path: str,
        category: str,
        page_range: Optional[Tuple[int, int]] = None
    ) -> Dict:
        run_id = str(uuid.uuid4())[:8]
        start_time = datetime.now(timezone.utc)
        pdf_path_obj = Path(pdf_path).resolve()
        doc_name = pdf_path_obj.name
        abs_pdf_path = str(pdf_path_obj)
        source_directory = pdf_path_obj.parent.name or pdf_path_obj.parent.as_posix()
        if not source_directory:
            source_directory = pdf_path_obj.parent.as_posix()
        source_category = source_directory or "未分组"
        if not category:
            category = source_category
        ctx = {
            "run_id": run_id,
            "pdf": doc_name,
            "engine": self.engine,
            "category": category,
            "source_category": source_category,
        }
        logger.info("ingest_start %s", ctx)
        doc_key = abs_pdf_path  # 用作 documents.document_id（唯一）

        force_reingest = os.getenv("FORCE_REINGEST", "0").lower() in {"1", "true"}

        # 幂等：整本且已有则跳过（除非强制）
        if not page_range:
            exist = self.mongodb_writer.documents.find_one({"document_id": doc_key}, projection={"_id": 1})
            if exist and not force_reingest:
                logger.info("skip_existing %s doc_id=%s", ctx, str(exist["_id"]))
                return {
                    "status": "skipped",
                    "reason": "document_id exists; set FORCE_REINGEST=1 or pass page_range",
                    "doc_key": doc_key,
                    "mongo_doc_id": str(exist["_id"]),
                }

        try:
            # 账本记录
            tp_local_probe = self._probe_total_pages(abs_pdf_path)
            tracker_record = self.tracker.records.get(doc_key)
            if not tracker_record:
                self.tracker.start_document(
                    file_path=abs_pdf_path,
                category=category,
                    total_pages=tp_local_probe if tp_local_probe > 0 else -1
                )
                tracker_record = self.tracker.records.get(abs_pdf_path)
            if tracker_record:
                tracker_record.engine = self.engine
                if tp_local_probe > 0:
                    tracker_record.total_pages = max(tracker_record.total_pages or -1, tp_local_probe)
                self.tracker._save_progress()

            # ===== Step 1: OCR =====
            with step_timer("ocr", ctx):
                ocr_kwargs = {}
                artifacts_dir = None
                if isinstance(self.ocr_client, MineruClient):
                    ocr_output_root = os.getenv("OCR_OUTPUT_DIR", "backend/databases/documents_ocr")
                    category_dir = os.path.join(ocr_output_root, category)
                    os.makedirs(category_dir, exist_ok=True)
                    safe_name = os.path.splitext(doc_name)[0]
                    doc_dir = os.path.join(category_dir, safe_name)
                    artifacts_dir = os.path.join(doc_dir, "mineru_outputs")
                    os.makedirs(artifacts_dir, exist_ok=True)
                    ocr_kwargs["artifacts_dir"] = artifacts_dir

                ocr_result = self.ocr_client.parse_pdf(pdf_path=pdf_path, page_range=page_range, **ocr_kwargs)

            result = ocr_result.get("result", {}) or {}
            total_pages = int(result.get("total_page_number") or 0)
            success_pages = int(result.get("success_count") or 0)
            metrics = ocr_result.get("metrics") or []
            conf_vals = [m.get("confidence") or m.get("score") for m in metrics if isinstance(m, dict) and (m.get("confidence") or m.get("score")) is not None]
            ocr_conf = float(sum(conf_vals) / len(conf_vals)) if conf_vals else 0.0

            # 账本已完成页段
            if page_range:
                self.tracker.merge_done_range(abs_pdf_path, category, int(page_range[0]), int(page_range[1]))
            else:
                self.tracker.merge_done_range(abs_pdf_path, category, 1, total_pages)

            # 保存 Markdown 与图片
            with step_timer("save_md_images", ctx):
                ocr_output_dir = os.getenv("OCR_OUTPUT_DIR", "backend/databases/documents_ocr")
                category_dir = os.path.join(ocr_output_dir, category)
                os.makedirs(category_dir, exist_ok=True)
                safe_name = os.path.splitext(doc_name)[0]
                doc_dir = os.path.join(category_dir, safe_name)
                os.makedirs(doc_dir, exist_ok=True)
                md_path = os.path.join(doc_dir, f"{safe_name}.md")

                md_text = result.get("markdown", "") or ""
                path_map = {}
                mineru_artifacts = ocr_result.get("artifacts_dir")

                if mineru_artifacts:
                    # 1) 可选拷贝图片（默认关闭，仅保留 md/_layout/_span）
                    # 默认开启图片拷贝；仅当 OCR_SAVE_IMAGES=0/false 时关闭
                    save_images = os.getenv("OCR_SAVE_IMAGES", "1").lower() not in {"0", "false"}
                    if save_images:
                        imgs = self._find_artifact_images(mineru_artifacts)
                        path_map = self._copy_images_fast(imgs, os.path.join(doc_dir, "images"))
                        md_text = self._rewrite_md_images(md_text, path_map)

                    # 2) 直接复用 MinerU 产物中的可视化 PDF（若存在，则拷贝到文档根目录）
                    self._generate_visual_pdfs(mineru_artifacts, abs_pdf_path, doc_dir, page_range)

                if page_range:
                    s, e = int(page_range[0]), int(page_range[1])
                    self._upsert_markdown_segment(md_path, s, e, md_text)
                else:
                    Path(md_path).write_text(md_text, encoding="utf-8")

                # 清理 MinerU 产物目录，只保留文档根目录下的 md、images、_layout/_span
                try:
                    keep_outputs = os.getenv("KEEP_MINERU_OUTPUTS", "0").lower() in {"1", "true"}
                    if mineru_artifacts and not keep_outputs:
                        DocumentIngestionPipeline._rmtree_force(mineru_artifacts)
                except Exception:
                    pass

            # ===== Step 2: 分块 =====
            with step_timer("chunking", ctx):
                doc_metadata = {
                    "type": category,
                    "title": doc_name,
                    "category": category,
                    "file_path": pdf_path,
                    "source_document": doc_name,
                    "source_category": source_category,
                    "source_directory": source_directory,
                    "source_path": abs_pdf_path,
                    "artifacts_dir": ocr_result.get("artifacts_dir"),  # ✨ 传递 artifacts_dir 用于图片提取
                }
                chunks = self.chunk_strategy.chunk_by_hierarchy(
                    textin_result=ocr_result,
                    doc_metadata=doc_metadata
                )
                for ch in chunks:
                    if "source_category" not in ch or not ch.get("source_category"):
                        ch["source_category"] = source_category
                    if "source_directory" not in ch or not ch.get("source_directory"):
                        ch["source_directory"] = source_directory
                    if "doc_source_category" not in ch or not ch.get("doc_source_category"):
                        ch["doc_source_category"] = source_category
                    if "doc_category" not in ch or not ch.get("doc_category"):
                        ch["doc_category"] = category
                    if "doc_title" not in ch or not ch.get("doc_title"):
                        ch["doc_title"] = doc_name
                # [DEPRECATED] 修正 image 路径为相对 images/... (已由 chunking.py 处理)
                # 注意：chunking.py 已经从 Markdown 提取图片并设置正确的 image_url_abs
                # 这里只处理未设置 image_url_abs 的情况（向后兼容）
                if 'path_map' in locals() and path_map:
                    name_map = {Path(k).name: v for k, v in path_map.items()}
                    abs_map = {Path(k).name: str(Path(k).resolve()) for k in path_map.keys()}
                    for ch in chunks:
                        if ch.get('content_type') == 'image' and not ch.get('image_url_abs'):
                            # 仅在 image_url_abs 未设置时才修正路径
                            url = ch.get('image_url') or ''
                            new_url = path_map.get(url) or name_map.get(Path(url).name)
                            if new_url:
                                ch['image_url'] = new_url
                                ch['image_url_abs'] = abs_map.get(Path(url).name, None)

            text_chunks = [c for c in chunks if c.get("content_type") == "text" and (c.get("content") or "").strip()]
            image_chunks = [c for c in chunks if c.get("content_type") == "image"]

            # ===== Step 2.5: ✨ VLM 图片描述生成 =====
            vlm_enabled = os.getenv("VLM_ENABLED", "1").lower() in {"1", "true"}
            vlm_processed = 0
            vlm_failed = 0
            vlm_skipped = 0

            def _parse_page_range_spec(spec: str) -> Optional[Tuple[int, int]]:
                try:
                    raw = (spec or "").strip().replace(" ", "")
                    if not raw:
                        return None
                    if "-" not in raw:
                        n = int(raw)
                        return (n, n)
                    a, b = raw.split("-", 1)
                    s, e = int(a), int(b)
                    if s <= 0 or e < s:
                        return None
                    return (s, e)
                except Exception:
                    return None

            # 可选：限制 VLM 处理的图片数量/页段，便于“局部到整体”迭代验证
            vlm_max_images = 0
            try:
                vlm_max_images = int(os.getenv("VLM_MAX_IMAGES_PER_DOC", "0") or 0)
            except Exception:
                vlm_max_images = 0
            vlm_page_rng = _parse_page_range_spec(os.getenv("VLM_IMAGE_PAGE_RANGE", ""))

            vlm_targets = list(image_chunks)
            if vlm_page_rng:
                s, e = int(vlm_page_rng[0]), int(vlm_page_rng[1])
                vlm_targets = [
                    c for c in vlm_targets
                    if isinstance(c.get("page_range"), list)
                    and c.get("page_range")
                    and s <= int(c.get("page_range")[0]) <= e
                ]
            # 稳定排序：按页码/序号，让每次限制都“先覆盖前几页”
            vlm_targets.sort(key=lambda c: (int((c.get("page_range") or [0])[0]), int(c.get("sequence") or 0)))

            if vlm_max_images and vlm_max_images > 0 and len(vlm_targets) > vlm_max_images:
                vlm_skipped = len(vlm_targets) - vlm_max_images
                vlm_targets = vlm_targets[:vlm_max_images]

            if vlm_enabled and vlm_targets:
                with step_timer("vlm_process_images", {**ctx, "count": len(vlm_targets)}):
                    logger.info(
                        "开始 VLM 处理图片: targets=%d total_images=%d max_per_doc=%s page_range=%s",
                        len(vlm_targets),
                        len(image_chunks),
                        str(vlm_max_images) if vlm_max_images else "unlimited",
                        f"{vlm_page_rng[0]}-{vlm_page_rng[1]}" if vlm_page_rng else "all",
                    )

                    for img_chunk in vlm_targets:
                        try:
                            img_abs_path = img_chunk.get("image_url_abs")
                            if not img_abs_path or not Path(img_abs_path).exists():
                                logger.warning(f"图片路径无效，跳过 VLM: {img_abs_path}")
                                vlm_failed += 1
                                continue

                            # 确保 metadata 为 dict（避免缺失导致 KeyError）
                            meta = img_chunk.get("metadata") or {}
                            if not isinstance(meta, dict):
                                meta = {}
                            img_chunk["metadata"] = meta

                            # ocr_text 优先使用 caption，避免把占位 content 传给 VLM
                            caption = (meta.get("caption") or "").strip()

                            # 调用 VLM 生成描述
                            vlm_caption = generate_image_description(
                                image_path=img_abs_path,
                                ocr_text=caption,
                                section=img_chunk.get("section", ""),
                                page=(img_chunk.get("page_range") or [0])[0],
                            )

                            # 更新 chunk content（后续会被向量化）
                            img_chunk["content"] = vlm_caption

                            # 仅当返回包含“正文描述”（即形如 "[图片] xxx" / "[图片: 图注] xxx"）才算真正 VLM 成功
                            ok = False
                            try:
                                if isinstance(vlm_caption, str):
                                    parts = vlm_caption.split("] ", 1)
                                    ok = len(parts) == 2 and bool(parts[1].strip())
                            except Exception:
                                ok = False

                            meta["vlm_processed"] = ok
                            if ok:
                                vlm_processed += 1
                            else:
                                vlm_failed += 1

                        except Exception as e:
                            logger.warning(f"VLM 处理失败: {e}, 使用原始 OCR 文本")
                            meta = img_chunk.get("metadata") or {}
                            if not isinstance(meta, dict):
                                meta = {}
                            img_chunk["metadata"] = meta
                            meta["vlm_processed"] = False
                            vlm_failed += 1

                    logger.info(
                        "VLM 处理完成: 成功=%d, 失败=%d, 跳过=%d",
                        vlm_processed,
                        vlm_failed,
                        vlm_skipped,
                    )

            # 现在图片 chunks 也有语义化的 content 了，加入向量化队列
            all_chunks_with_content = text_chunks + [c for c in image_chunks if (c.get("content") or "").strip()]

            # ===== Step 3: 先写 documents（不带 chunks），拿 doc_id =====
            with step_timer("mongo_write_document", ctx):
                total_pages_for_doc = (self.tracker.records.get(doc_key).total_pages if self.tracker.records.get(doc_key) else total_pages)
                if page_range:
                    page_rng = [int(page_range[0]), int(page_range[1])]
                else:
                    page_rng = [1, total_pages_for_doc]

                full_doc_metadata = {
                    "document_id": doc_key,
                    "title": doc_name,
                    "source_document": doc_name,
                    "type": category,
                    "category": category,
                    "source_category": source_category,
                    "source_directory": source_directory,
                    "source_path": abs_pdf_path,
                    "total_pages": total_pages_for_doc,
                    "page_range": page_rng,
                    "ocr_engine": self.engine,
                    "ocr_confidence": ocr_conf,
                    "upload_time": datetime.now(timezone.utc),
                }

                # 如果强制重新索引，先删除旧数据
                if force_reingest:
                    old_doc = self.mongodb_writer.documents.find_one({"document_id": doc_key}, projection={"_id": 1})
                    if old_doc:
                        old_doc_id = old_doc["_id"]
                        logger.info("force_reingest: deleting old doc_id=%s and its chunks", str(old_doc_id))
                        try:
                            deleted = self.milvus_writer.delete_by_doc_id(str(old_doc_id))
                            logger.info("force_reingest: deleted milvus vectors doc_id=%s count=%s", str(old_doc_id), deleted)
                        except Exception as exc:
                            logger.warning("force_reingest: delete milvus vectors failed doc_id=%s err=%s", str(old_doc_id), exc)
                        # 删除旧的 chunks
                        self.mongodb_writer.chunks.delete_many({"doc_id": old_doc_id})
                        # 删除旧的 document
                        self.mongodb_writer.documents.delete_one({"_id": old_doc_id})

                mongo_doc_id = self.mongodb_writer.documents.insert_one(full_doc_metadata).inserted_id

            # ===== Step 4: 向量化 + 分批写入 Milvus 与 Mongo chunks（Mongo 不存 embedding） =====
            embeddings_written = 0
            chunks_inserted = 0
            chunks_skipped = 0

            # ✨ 使用包含图片的完整列表进行向量化
            texts = [c["content"] for c in all_chunks_with_content]
            unique_texts = list(dict.fromkeys(texts).keys())

            def pick_bs(sizes, max_chars=120000, hard_cap=200):
                total, bs = 0, 0
                for sz in sizes:
                    if total + sz > max_chars and bs > 0:
                        break
                    total += sz; bs += 1
                return max(1, min(bs, hard_cap))

            u = 0
            batch_id = 0
            while u < len(unique_texts):
                lookahead = unique_texts[u:u+200]
                sizes = [len(t) for t in lookahead]
                bs = pick_bs(sizes)
                batch = unique_texts[u:u+bs]; u += bs; batch_id += 1

                # 4.1 计算 embedding
                with step_timer("embed_batch", {**ctx, "batch": batch_id, "size": len(batch)}):
                    embs = self.embedding_generator.generate_batch(batch, batch_size=min(len(batch), 100))
                    emb_map = dict(zip(batch, embs))

                # 4.2 回填到所有 chunks（包括图片），并收集本批"新填充"的 chunks
                filled_batch: List[Dict] = []
                mongo_ops: List[Dict] = []

                for ch in all_chunks_with_content:
                    if "embedding" in ch:
                        continue
                    t = ch["content"]
                    e = emb_map.get(t)
                    if e is not None:
                        ch["embedding"] = e
                        filled_batch.append(ch)

                # 4.3 先写 Milvus（只写有 embedding 的）
                if filled_batch:
                    with step_timer("milvus_insert_batch", {**ctx, "count": len(filled_batch)}):
                        self.milvus_writer.insert_vectors(chunks=filled_batch, doc_id=str(mongo_doc_id))
                        embeddings_written += len(filled_batch)

                # 4.4 写 Mongo chunks（不存 embedding）
                for ch in filled_batch:
                    item = dict(ch)
                    item["doc_id"] = mongo_doc_id
                    item.pop("embedding", None)  # Mongo 不存 embedding
                    mongo_ops.append(item)

                # ✨ 最后一批：把未向量化的图片（VLM失败的）也入库
                if u == len(unique_texts):
                    for ch in image_chunks:
                        if "embedding" not in ch:  # 只加未向量化的
                            item = dict(ch); item["doc_id"] = mongo_doc_id
                            mongo_ops.append(item)

                if mongo_ops:
                    from pymongo import InsertOne
                    ops = [InsertOne(op) for op in mongo_ops]
                    ins, skip = self.mongodb_writer._bulk_insert(ops, doc_id=str(mongo_doc_id))  # 返回 (inserted, skipped)
                    chunks_inserted += ins
                    chunks_skipped += skip

            # 没有文本 chunk 时：也把图片等入库
            if not unique_texts and image_chunks:
                from pymongo import InsertOne
                ops = []
                for ch in image_chunks:
                    item = dict(ch); item["doc_id"] = mongo_doc_id
                    ops.append(InsertOne(item))
                ins, skip = self.mongodb_writer._bulk_insert(ops, doc_id=str(mongo_doc_id))
                chunks_inserted += ins
                chunks_skipped += skip

            # ===== Step 5: 完成标记 =====
            if not page_range:
                self.tracker.complete_document(
                    doc_key=doc_key,
                    mongo_doc_id=str(mongo_doc_id),
                    total_chunks=len(chunks)
                )
            
            end_time = datetime.now(timezone.utc)
            duration = (end_time - start_time).total_seconds()
            processed_pages = success_pages or (page_range[1] - page_range[0] + 1 if page_range else (total_pages or 1))
            processed_pages = max(int(processed_pages), 1)

            result_payload = {
                "status": "success",
                "run_id": run_id,
                "doc_key": doc_key,
                "mongo_doc_id": str(mongo_doc_id),
                "engine": self.engine,
                "total_pages": total_pages,
                "total_chunks": len(chunks),
                "text_chunks": len(text_chunks),
                "image_chunks": len(image_chunks),
                "vlm_processed": vlm_processed if vlm_enabled else 0,  # ✨ VLM统计
                "vlm_failed": vlm_failed if vlm_enabled else 0,
                "vlm_skipped": vlm_skipped if vlm_enabled else 0,
                "embeddings_written": embeddings_written,
                "chunks_inserted": chunks_inserted,
                "chunks_skipped": chunks_skipped,
                "timings": {
                    "total_s": duration,
                    "avg_per_page_s": duration / processed_pages,
                },
            }
            logger.info("ingest_done %s %s", ctx, result_payload)
            return result_payload
            
        except Exception as e:
            logger.exception("[FAIL] 处理失败 %s error=%s", ctx, e)
            try:
                self.tracker.fail_document(doc_key, str(e))
            except Exception:
                pass
            return {"status": "failed", "error": str(e), "run_id": run_id}
    
    def close(self):
        """关闭所有连接"""
        try:
            self.mongodb_writer.close()
        except Exception:
            pass
        logger.info("[OK] Pipeline closed")
