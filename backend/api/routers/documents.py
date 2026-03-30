"""Document serving endpoints."""

from functools import lru_cache
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DOCUMENTS_DIR = Path(
    os.getenv("DATA_PROCESS_DOCUMENTS_DIR", str(PROJECT_ROOT / "data_process" / "documents"))
).resolve()
OCR_OUTPUT_DIR = Path(
    os.getenv("DATA_PROCESS_OCR_DIR", str(PROJECT_ROOT / "data_process" / "documents_ocr"))
).resolve()

router = APIRouter()


def _strip_legacy_storage_prefix(path: str, marker: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    if not normalized:
        return ""

    lowered = normalized.lower()
    marker_lower = marker.lower()
    idx = lowered.find(marker_lower)
    if idx >= 0:
        return normalized[idx + len(marker) :].lstrip("/")

    return normalized.lstrip("/")


def _resolve_image_candidate(requested_path: str) -> Path | None:
    """Resolve an OCR image path under OCR_OUTPUT_DIR.

    Tolerates both current `.../full/images/<file>` layout and legacy
    `.../images/<file>` references emitted by older indices.
    """
    if not requested_path:
        return None

    normalized = _strip_legacy_storage_prefix(requested_path, "documents_ocr/")
    parts = [p for p in normalized.split("/") if p]
    if not parts or ".." in parts:
        return None

    candidates: list[Path] = []

    direct = (OCR_OUTPUT_DIR / Path(*parts)).resolve()
    candidates.append(direct)

    if len(parts) >= 4 and parts[2] == "images":
        with_full = (OCR_OUTPUT_DIR / Path(parts[0], parts[1], "full", *parts[2:])).resolve()
        candidates.append(with_full)

    for candidate in candidates:
        try:
            candidate.relative_to(OCR_OUTPUT_DIR)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate

    return None


@lru_cache(maxsize=2048)
def _resolve_pdf_candidate(requested_path: str) -> Path | None:
    """Resolve a requested PDF path to an existing file under DOCUMENTS_DIR.

    This is intentionally tolerant to minor path mismatches (e.g. wrong top-level
    category folder) so old/dirty indices won't break PDF preview.
    """
    if not requested_path:
        return None

    import glob

    normalized = _strip_legacy_storage_prefix(requested_path, "documents/")

    # 1) Direct hit
    direct = (DOCUMENTS_DIR / normalized).resolve()
    try:
        direct.relative_to(DOCUMENTS_DIR)
    except ValueError:
        pass
    else:
        if direct.is_file():
            return direct

    if direct.is_file():
        return direct

    parts = [p for p in normalized.split("/") if p]
    if not parts:
        return None

    # 2) If the first segment is wrong, try replacing it with an existing top dir
    if len(parts) >= 2 and DOCUMENTS_DIR.exists():
        tail = Path(*parts[1:])
        for top in DOCUMENTS_DIR.iterdir():
            if not top.is_dir():
                continue
            cand = (top / tail).resolve()
            try:
                cand.relative_to(DOCUMENTS_DIR)
            except ValueError:
                continue
            if cand.is_file():
                return cand

    # 3) Last resort: search by filename (only if it's unique)
    filename = parts[-1]
    base = Path(filename).stem
    suffix = Path(filename).suffix

    variants: list[str] = []

    def _add(name: str) -> None:
        name = str(name or "").strip()
        if not name or name in variants:
            return
        variants.append(name)

    _add(filename)

    # OCR/markdown indices sometimes point to .md; map to same-name PDF if available.
    if suffix.lower() == ".md" and base:
        _add(f"{base}.pdf")

    # Tolerate Chinese book-title brackets mismatch: 《title》.pdf <-> title.pdf
    stripped = base.replace("《", "").replace("》", "").strip() if base else ""
    if stripped and stripped != base:
        _add(f"{stripped}{suffix}")
        _add(f"{stripped}.pdf")
    elif base and suffix.lower() in (".pdf", ".md"):
        _add(f"《{base}》.pdf" if suffix.lower() == ".md" else f"《{base}》{suffix}")

    # Tolerate whitespace mismatch (some titles are stored without spaces).
    if base and (" " in base):
        compact = base.replace(" ", "")
        _add(f"{compact}{suffix}")
        _add(f"{compact}.pdf")
        _add(f"《{compact}》.pdf")

    for variant in variants:
        matches: list[Path] = []
        for hit in DOCUMENTS_DIR.rglob(glob.escape(variant)):
            if not hit.is_file():
                continue
            resolved = hit.resolve()
            try:
                resolved.relative_to(DOCUMENTS_DIR)
            except ValueError:
                continue
            matches.append(resolved)
            if len(matches) > 1:
                break
        if len(matches) == 1:
            return matches[0]

    return None


@router.get("/documents/pdf", summary="预览 PDF 文档")
async def serve_pdf(path: str = Query(..., description="相对 backend/databases/documents 的文件路径")):
    """Stream a PDF file from the documents directory."""
    if not path:
        raise HTTPException(status_code=400, detail="缺少文件路径")

    candidate = _resolve_pdf_candidate(path)
    if candidate is None:
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(candidate, media_type="application/pdf", filename=candidate.name)


@router.get("/documents/image", summary="获取 OCR 提取的图片")
async def serve_image(path: str = Query(..., description="相对 backend/databases/documents_ocr 的文件路径")):
    """
    Serve an image file from the OCR output directory.

    支持的图片格式: JPG, PNG, WEBP

    示例:
    - /api/v1/documents/image?path=标准规范/GB 51039-2014/images/xxx.jpg
    """
    if not path:
        raise HTTPException(status_code=400, detail="缺少文件路径")

    candidate = _resolve_image_candidate(path)
    if candidate is None:
        raise HTTPException(status_code=404, detail="文件不存在")

    # 根据文件扩展名设置 MIME 类型
    suffix = candidate.suffix.lower()
    media_type_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }
    media_type = media_type_map.get(suffix, "application/octet-stream")

    return FileResponse(candidate, media_type=media_type, filename=candidate.name)
