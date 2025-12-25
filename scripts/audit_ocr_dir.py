"""
审计 backend/databases/documents_ocr 的结构与资源完整性。

目标：
- 快速知道每份资料是否有 Markdown、是否有 images 目录、图片数量大概是多少
- 找出“缺 md / 缺 images / 0 图片”的异常项，便于决定是否需要重跑 OCR/索引

用法（在项目根目录）：
  python scripts/audit_ocr_dir.py
  python scripts/audit_ocr_dir.py --category 书籍报告
  python scripts/audit_ocr_dir.py --csv ocr_audit.csv
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
OCR_ROOT = (PROJECT_ROOT / "backend" / "databases" / "documents_ocr").resolve()


@dataclass
class DocAudit:
    category: str
    doc_folder: str
    md_exists: bool
    md_size: int
    images_dir_exists: bool
    image_count: int
    layout_pdf_exists: bool
    span_pdf_exists: bool


def _iter_docs(category_filter: Optional[str]) -> Iterable[tuple[str, Path]]:
    if not OCR_ROOT.exists():
        return []

    for category_dir in sorted([p for p in OCR_ROOT.iterdir() if p.is_dir()]):
        category = category_dir.name
        if category_filter and category != category_filter:
            continue
        for doc_dir in sorted([p for p in category_dir.iterdir() if p.is_dir()]):
            yield category, doc_dir


def audit(category_filter: Optional[str]) -> List[DocAudit]:
    rows: List[DocAudit] = []
    for category, doc_dir in _iter_docs(category_filter):
        doc_name = doc_dir.name
        md_path = doc_dir / f"{doc_name}.md"
        images_dir = doc_dir / "images"
        layout_pdf = doc_dir / "_layout.pdf"
        span_pdf = doc_dir / "_span.pdf"

        md_exists = md_path.is_file()
        md_size = md_path.stat().st_size if md_exists else 0

        images_dir_exists = images_dir.is_dir()
        image_count = 0
        if images_dir_exists:
            image_count = sum(1 for p in images_dir.iterdir() if p.is_file())

        rows.append(
            DocAudit(
                category=category,
                doc_folder=doc_name,
                md_exists=md_exists,
                md_size=md_size,
                images_dir_exists=images_dir_exists,
                image_count=image_count,
                layout_pdf_exists=layout_pdf.is_file(),
                span_pdf_exists=span_pdf.is_file(),
            )
        )
    return rows


def _print_summary(rows: List[DocAudit]) -> None:
    total = len(rows)
    missing_md = sum(1 for r in rows if not r.md_exists)
    missing_images_dir = sum(1 for r in rows if not r.images_dir_exists)
    zero_images = sum(1 for r in rows if r.images_dir_exists and r.image_count == 0)

    print("=" * 80)
    print("documents_ocr 审计结果")
    print("=" * 80)
    print(f"OCR 根目录: {OCR_ROOT}")
    print(f"总资料数: {total}")
    print(f"缺少 Markdown: {missing_md}")
    print(f"缺少 images 目录: {missing_images_dir}")
    print(f"images 目录存在但 0 图片: {zero_images}")

    print("\n异常清单（只显示前 30 条）")
    print("-" * 80)
    shown = 0
    for r in rows:
        bad = (not r.md_exists) or (not r.images_dir_exists) or (r.images_dir_exists and r.image_count == 0)
        if not bad:
            continue
        print(
            f"- {r.category}/{r.doc_folder} | "
            f"md={'OK' if r.md_exists else 'MISSING'} "
            f"images={'OK' if r.images_dir_exists else 'MISSING'} "
            f"img_count={r.image_count}"
        )
        shown += 1
        if shown >= 30:
            break
    if shown == 0:
        print("(无)")

    print("\nTop 20（按图片数量）")
    print("-" * 80)
    top = sorted(rows, key=lambda x: x.image_count, reverse=True)[:20]
    for r in top:
        print(f"- {r.category}/{r.doc_folder} | img_count={r.image_count} | md_size={r.md_size}")


def _write_csv(rows: List[DocAudit], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "category",
                "doc_folder",
                "md_exists",
                "md_size",
                "images_dir_exists",
                "image_count",
                "layout_pdf_exists",
                "span_pdf_exists",
            ]
        )
        for r in rows:
            w.writerow(
                [
                    r.category,
                    r.doc_folder,
                    int(r.md_exists),
                    r.md_size,
                    int(r.images_dir_exists),
                    r.image_count,
                    int(r.layout_pdf_exists),
                    int(r.span_pdf_exists),
                ]
            )
    print(f"\n已写出 CSV: {csv_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit documents_ocr directory")
    parser.add_argument("--category", type=str, default=None, help="仅审计指定类别（如 书籍报告/参考论文）")
    parser.add_argument("--csv", type=str, default=None, help="输出 CSV 路径（可选）")
    args = parser.parse_args()

    rows = audit(args.category)
    _print_summary(rows)

    if args.csv:
        _write_csv(rows, Path(args.csv).resolve())


if __name__ == "__main__":
    main()

