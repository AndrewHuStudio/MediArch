from pathlib import Path
import importlib
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_documents_pdf_endpoint_serves_from_data_process_documents_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("DATA_PROCESS_DOCUMENTS_DIR", str(tmp_path))

    import backend.api.routers.documents as documents

    reloaded = importlib.reload(documents)
    pdf_path = tmp_path / "参考论文" / "既有大型综合医院门诊部功能布局优化设计研究_呙俊.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    app = FastAPI()
    app.include_router(reloaded.router, prefix="/api/v1")
    client = TestClient(app)

    response = client.get(
        "/api/v1/documents/pdf",
        params={"path": "参考论文/既有大型综合医院门诊部功能布局优化设计研究_呙俊.pdf"},
    )

    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 fake"


def test_documents_router_reads_pdf_dir_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("DATA_PROCESS_DOCUMENTS_DIR", str(tmp_path))

    import backend.api.routers.documents as documents

    reloaded = importlib.reload(documents)

    assert reloaded.DOCUMENTS_DIR == tmp_path.resolve()


def test_mongodb_search_computes_relative_path_under_data_process_documents_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("DATA_PROCESS_DOCUMENTS_DIR", str(tmp_path))

    import backend.app.services.mongodb_search as mongodb_search

    reloaded = importlib.reload(mongodb_search)
    retriever = reloaded.MongoDBChunkRetriever.__new__(reloaded.MongoDBChunkRetriever)

    pdf_path = (tmp_path / "书籍报告" / "医院建筑设计指南.pdf").resolve()
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    result = retriever._compute_relative_path(str(pdf_path))

    assert result == "书籍报告/医院建筑设计指南.pdf"


def test_mongodb_search_computes_relative_path_from_legacy_absolute_path(tmp_path, monkeypatch):
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("DATA_PROCESS_DOCUMENTS_DIR", str(tmp_path))

    import backend.app.services.mongodb_search as mongodb_search

    reloaded = importlib.reload(mongodb_search)
    retriever = reloaded.MongoDBChunkRetriever.__new__(reloaded.MongoDBChunkRetriever)

    legacy_path = r"E:\MyPrograms\250804-MediArch System\backend\databases\documents\书籍报告\医院建筑设计指南.pdf"

    result = retriever._compute_relative_path(legacy_path)

    assert result == "书籍报告/医院建筑设计指南.pdf"


def test_mongodb_search_infers_file_path_from_title_and_category(tmp_path, monkeypatch):
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("DATA_PROCESS_DOCUMENTS_DIR", str(tmp_path))

    import backend.app.services.mongodb_search as mongodb_search

    reloaded = importlib.reload(mongodb_search)
    retriever = reloaded.MongoDBChunkRetriever.__new__(reloaded.MongoDBChunkRetriever)

    pdf_path = (tmp_path / "书籍报告" / "医疗功能房间详图集3.pdf").resolve()
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    result = retriever._infer_file_path("医疗功能房间详图集3", "书籍报告")

    assert result == "书籍报告/医疗功能房间详图集3.pdf"


def test_documents_pdf_endpoint_accepts_legacy_absolute_path(tmp_path, monkeypatch):
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("DATA_PROCESS_DOCUMENTS_DIR", str(tmp_path))

    import backend.api.routers.documents as documents

    reloaded = importlib.reload(documents)
    pdf_path = tmp_path / "书籍报告" / "医院建筑设计指南.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    app = FastAPI()
    app.include_router(reloaded.router, prefix="/api/v1")
    client = TestClient(app)

    legacy_path = r"E:\MyPrograms\250804-MediArch System\backend\databases\documents\书籍报告\医院建筑设计指南.pdf"
    response = client.get("/api/v1/documents/pdf", params={"path": legacy_path})

    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 fake"
