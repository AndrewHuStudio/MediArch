"""Document serving endpoints."""

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DOCUMENTS_DIR = (PROJECT_ROOT / "backend" / "databases" / "documents").resolve()
OCR_OUTPUT_DIR = (PROJECT_ROOT / "backend" / "databases" / "documents_ocr").resolve()

router = APIRouter()


@lru_cache(maxsize=2048)
def _resolve_pdf_candidate(requested_path: str) -> Path | None:
    """Resolve a requested PDF path to an existing file under DOCUMENTS_DIR.

    This is intentionally tolerant to minor path mismatches (e.g. wrong top-level
    category folder) so old/dirty indices won't break PDF preview.
    """
    if not requested_path:
        return None

    normalized = requested_path.strip().lstrip("/").replace("\\", "/")

    # 1) Direct hit
    direct = (DOCUMENTS_DIR / normalized).resolve()
    try:
        direct.relative_to(DOCUMENTS_DIR)
    except ValueError:
        return None
    if direct.is_file():
        return direct

    parts = [p for p in normalized.split("/") if p]
    if not parts:
        return None

    # 2) If the first segment is wrong, try replacing it with an existing top dir
    if len(parts) >= 2:
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
    matches: list[Path] = []
    for hit in DOCUMENTS_DIR.rglob(filename):
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

    # 安全检查：防止路径遍历攻击
    candidate = (OCR_OUTPUT_DIR / path).resolve()
    try:
        candidate.relative_to(OCR_OUTPUT_DIR)
    except ValueError:
        raise HTTPException(status_code=404, detail="文件不存在或路径非法")

    if not candidate.is_file():
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
