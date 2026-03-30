"""
模块1: OCR 处理

封装现有 MineruClient，提供统一的 OCR 处理接口。
支持单文件和批量处理，带进度回调。
"""

import logging
from typing import Optional, Tuple, Dict, Any, List, Callable
from pathlib import Path
from dataclasses import dataclass

from backend.databases.ingestion.ocr.mineru_client import MineruClient
from backend.databases.ingestion.ocr.ocr_progress_tracker import OCRProgressTracker

logger = logging.getLogger(__name__)


@dataclass
class OcrResult:
    """OCR 处理结果"""
    file_name: str
    markdown: str
    detail: list
    total_pages: int
    success_pages: int
    duration_ms: int
    artifacts_dir: Optional[str] = None


class OcrModule:
    """OCR 处理模块 -- 封装 MineruClient + OCRProgressTracker"""

    def __init__(self, output_dir: str = "data_process/documents_ocr"):
        self.client = MineruClient()
        self.tracker = OCRProgressTracker()
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def process_pdf(
        self,
        pdf_path: str,
        category: str = "",
        page_range: Optional[Tuple[int, int]] = None,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> OcrResult:
        """处理单个 PDF 文件。

        Args:
            pdf_path: PDF 文件绝对路径
            category: 文档分类 (如 "标准规范")
            page_range: 可选页码范围 (start, end)，1-based
            progress_callback: fn(status, current, total)

        Returns:
            OcrResult
        """
        pdf = Path(pdf_path).resolve()
        if not pdf.exists():
            raise FileNotFoundError(f"PDF not found: {pdf}")

        safe_name = pdf.stem
        base_artifacts = self.output_dir / (category or "uncategorized") / safe_name
        if page_range and isinstance(page_range, tuple):
            s, e = int(page_range[0]), int(page_range[1])
            artifacts_dir = str(base_artifacts / f"p{s}-{e}")
        else:
            artifacts_dir = str(base_artifacts)

        if progress_callback:
            progress_callback("prepare", 1, 5)

        logger.info("OCR start: %s (category=%s, pages=%s)", pdf.name, category, page_range)
        if progress_callback:
            progress_callback("dispatch", 2, 5)

        ocr_raw = self.client.parse_pdf(
            pdf_path=str(pdf),
            page_range=page_range,
            artifacts_dir=artifacts_dir,
        )
        if progress_callback:
            progress_callback("parse_done", 4, 5)

        result_data = ocr_raw.get("result", {})

        # 更新进度追踪
        total_pages = int(result_data.get("total_page_number") or 0)
        try:
            self.tracker.merge_done_range(
                str(pdf), category,
                page_range[0] if page_range else 1,
                page_range[1] if page_range else max(total_pages, 1),
            )
        except Exception as e:
            logger.warning("Progress tracker update failed: %s", e)

        if progress_callback:
            progress_callback("ocr_done", 5, 5)

        logger.info("OCR done: %s, pages=%d, duration=%dms",
                     pdf.name, total_pages, int(ocr_raw.get("duration", 0)))

        return OcrResult(
            file_name=pdf.name,
            markdown=result_data.get("markdown", ""),
            detail=result_data.get("detail", []),
            total_pages=total_pages,
            success_pages=int(result_data.get("success_count") or 0),
            duration_ms=int(ocr_raw.get("duration", 0)),
            artifacts_dir=ocr_raw.get("artifacts_dir"),
        )

    def process_batch(
        self,
        pdf_paths: List[str],
        category: str = "",
        progress_callback: Optional[Callable[[str, int, int, str], None]] = None,
    ) -> List[OcrResult]:
        """批量处理多个 PDF。

        Args:
            pdf_paths: PDF 文件路径列表
            category: 文档分类
            progress_callback: fn(status, current_idx, total, current_file)
        """
        results = []
        total = len(pdf_paths)
        for i, pdf_path in enumerate(pdf_paths):
            fname = Path(pdf_path).name
            if progress_callback:
                progress_callback("batch_progress", i, total, fname)
            try:
                result = self.process_pdf(pdf_path, category=category)
                results.append(result)
            except Exception as e:
                logger.error("Batch OCR failed for %s: %s", fname, e)
                results.append(OcrResult(
                    file_name=fname, markdown="", detail=[],
                    total_pages=0, success_pages=0, duration_ms=0,
                ))
        if progress_callback:
            progress_callback("batch_done", total, total, "")
        return results

    def get_progress(self) -> Dict[str, Any]:
        """返回 OCR 整体进度统计。"""
        try:
            return self.tracker.get_overall_stats()
        except Exception:
            return {
                "total_documents": 0, "completed": 0, "processing": 0,
                "failed": 0, "total_pages": 0, "scanned_pages": 0, "total_chunks": 0,
            }
