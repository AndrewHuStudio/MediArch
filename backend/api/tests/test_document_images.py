from pathlib import Path
import importlib
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_to_ocr_image_rel_path_preserves_full_images_path():
    import os

    os.environ["DEBUG"] = "true"
    from backend.api.routers.chat import _to_ocr_image_rel_path

    result = _to_ocr_image_rel_path(
        "书籍报告/医院建筑设计指南/full/images/example.jpg",
        {"source": "医院建筑设计指南.pdf", "doc_category": "书籍报告"},
    )

    assert result == "书籍报告/医院建筑设计指南/full/images/example.jpg"


def test_documents_image_endpoint_serves_from_data_process_ocr_dir(tmp_path, monkeypatch):
    import os

    os.environ["DEBUG"] = "true"
    from backend.api.routers import documents

    image_path = tmp_path / "书籍报告" / "医院建筑设计指南" / "full" / "images" / "example.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"fake-image")

    monkeypatch.setattr(documents, "OCR_OUTPUT_DIR", tmp_path.resolve())

    app = FastAPI()
    app.include_router(documents.router, prefix="/api/v1")
    client = TestClient(app)
    response = client.get(
        "/api/v1/documents/image",
        params={"path": "书籍报告/医院建筑设计指南/images/example.jpg"},
    )

    assert response.status_code == 200
    assert response.content == b"fake-image"


def test_documents_router_reads_ocr_dir_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_PROCESS_OCR_DIR", str(tmp_path))

    import backend.api.routers.documents as documents

    reloaded = importlib.reload(documents)

    assert reloaded.OCR_OUTPUT_DIR == tmp_path.resolve()


def test_documents_image_endpoint_accepts_legacy_absolute_path(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_PROCESS_OCR_DIR", str(tmp_path))

    import backend.api.routers.documents as documents

    reloaded = importlib.reload(documents)
    image_path = tmp_path / "书籍报告" / "医院建筑设计指南" / "full" / "images" / "example.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"fake-image")

    app = FastAPI()
    app.include_router(reloaded.router, prefix="/api/v1")
    client = TestClient(app)

    legacy_path = r"E:\MyPrograms\250804-MediArch System\backend\databases\documents_ocr\书籍报告\医院建筑设计指南\full\images\example.jpg"
    response = client.get("/api/v1/documents/image", params={"path": legacy_path})

    assert response.status_code == 200
    assert response.content == b"fake-image"
