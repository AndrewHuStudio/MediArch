import asyncio
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from data_process import api as data_process_api
from data_process.schemas import TaskStatus


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def single(self):
        return self._row


class _FakeSession:
    def __init__(self, calls):
        self.calls = calls
        self.non_skeleton_deleted = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def run(self, query):
        normalized = " ".join(str(query).split())
        self.calls.append(normalized)

        if "WHERE n.seed_source IS NULL AND (n.is_concept IS NULL OR n.is_concept = false) RETURN count(n) as count" in normalized:
            return _FakeResult({"count": 7})
        if "MATCH (n) RETURN count(n) as count" in normalized:
            return _FakeResult({"count": 5 if self.non_skeleton_deleted else 12})
        if "MATCH ()-[r]->() RETURN count(r) as count" in normalized:
            return _FakeResult({"count": 9 if self.non_skeleton_deleted else 20})
        if "WHERE n.seed_source IS NOT NULL OR n.is_concept = true" in normalized:
            return _FakeResult({"count": 5})
        if "WHERE n.seed_source IS NULL AND (n.is_concept IS NULL OR n.is_concept = false) DETACH DELETE n" in normalized:
            self.non_skeleton_deleted = True
            return _FakeResult({"count": 7})
        return _FakeResult({"count": 0})


class _FakeDriver:
    def __init__(self, calls):
        self.calls = calls
        self.closed = False

    def session(self, database=None):
        return _FakeSession(self.calls)

    def close(self):
        self.closed = True


class _FakeUpdateResult:
    def __init__(self, modified_count):
        self.modified_count = modified_count


class _FakeCollection:
    def __init__(self):
        self.updates = []

    def count_documents(self, query):
        if query == {}:
            return 12
        if query == {"kg_processed": True}:
            return 4
        return 0

    def update_many(self, query, update):
        self.updates.append((query, update))
        return _FakeUpdateResult(modified_count=4)


class _FakeDatabase:
    def __init__(self, collection):
        self.collection = collection

    def __getitem__(self, _name):
        return self.collection

    def get_collection(self, _name):
        return self.collection


class _FakeMongoClient:
    def __init__(self, collection):
        self.collection = collection
        self.closed = False

    def __getitem__(self, _name):
        return _FakeDatabase(self.collection)

    def close(self):
        self.closed = True


def test_clear_kg_keep_skeleton_preserves_seed_nodes_and_clears_processed_flags(monkeypatch):
    calls = []
    collection = _FakeCollection()
    mongo_client = _FakeMongoClient(collection)
    driver = _FakeDriver(calls)

    monkeypatch.setattr(data_process_api, "_create_neo4j_driver", lambda: driver, raising=False)
    monkeypatch.setattr(data_process_api, "_create_mongo_client", lambda: mongo_client, raising=False)
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
    monkeypatch.setenv("MONGODB_DATABASE", "mediarch")
    monkeypatch.setenv("MONGODB_CHUNK_COLLECTION", "mediarch_chunks")

    result = data_process_api._clear_kg_keep_skeleton()

    assert result["neo4j"]["deleted_nodes"] == 7
    assert result["neo4j"]["deleted_relationships"] == 11
    assert result["neo4j"]["preserved_skeleton_nodes"] == 5
    assert result["neo4j"]["remaining_relationships"] == 9
    assert result["mongodb"]["processed_chunks_cleared"] == 4
    assert any(
        "WHERE n.seed_source IS NULL AND (n.is_concept IS NULL OR n.is_concept = false)" in call
        for call in calls
    )
    assert any("DETACH DELETE n" in call for call in calls)
    assert all("MATCH ()-[r]->() DELETE r" not in call for call in calls)
    assert collection.updates == [
        (
            {"kg_processed": True},
            {"$unset": {"kg_processed": "", "kg_processed_at": ""}},
        )
    ]


def test_get_task_status_includes_created_at(monkeypatch):
    task_id = "kg-created-at"
    data_process_api._tasks.clear()
    data_process_api._tasks[task_id] = {
        "status": TaskStatus.RUNNING,
        "module": "kg",
        "progress": {"stage": "ea_recognition", "current": 1, "total": 5},
        "result": None,
        "error": None,
        "created_at": "2026-03-24T10:00:00",
    }
    monkeypatch.setattr(data_process_api, "_save_tasks", lambda: None)

    result = asyncio.run(data_process_api.get_task_status(task_id))

    assert result.created_at == "2026-03-24T10:00:00"


def test_get_task_status_includes_resume_payload(monkeypatch):
    task_id = "kg-resume-payload"
    data_process_api._tasks.clear()
    data_process_api._tasks[task_id] = {
        "status": TaskStatus.RUNNING,
        "module": "kg",
        "progress": {"stage": "cross_document_fusion", "current": 2, "total": 3},
        "result": None,
        "error": None,
        "created_at": "2026-03-24T10:00:00",
        "resume_payload": {
            "resume_from_stage": "cross_document_fusion",
            "stage4_checkpoint": {
                "substage": "neo4j_write",
                "write_progress": {"processed_count": 12},
            },
        },
    }
    monkeypatch.setattr(data_process_api, "_save_tasks", lambda: None)

    result = asyncio.run(data_process_api.get_task_status(task_id))

    assert result.resume_payload == {
        "resume_from_stage": "cross_document_fusion",
        "stage4_checkpoint": {
            "substage": "neo4j_write",
            "write_progress": {"processed_count": 12},
        },
    }
