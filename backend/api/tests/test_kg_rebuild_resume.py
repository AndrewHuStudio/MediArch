import sys
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from data_process import api as data_process_api


class _FakeCollection:
    def __init__(self, docs):
        self.docs = list(docs)

    def find(self, query, projection):
        allowed_types = set((query.get("content_type") or {}).get("$in") or [])
        allowed_doc_ids = query.get("doc_id", {}).get("$in")
        rows = []
        for doc in self.docs:
            if allowed_types and doc.get("content_type") not in allowed_types:
                continue
            if allowed_doc_ids is not None and doc.get("doc_id") not in allowed_doc_ids:
                continue
            row = {}
            for key, enabled in (projection or {}).items():
                if enabled and key in doc:
                    row[key] = doc[key]
            row["_id"] = doc.get("_id")
            rows.append(row)
        return rows


class _FakeDB:
    def __init__(self, docs):
        self._collection = _FakeCollection(docs)

    def get_collection(self, name):
        assert name == "mediarch_chunks"
        return self._collection


def test_load_chunks_from_builder_db_reuses_kg_builder_connection():
    docs = [
        {
            "_id": "mongo-1",
            "chunk_id": "chunk-1",
            "content": "门诊部应靠近医院主入口，方便患者到达。",
            "content_type": "text",
            "doc_id": "doc-1",
            "section": "总则",
            "source_document": "标准规范/a.pdf",
        },
        {
            "_id": "mongo-2",
            "chunk_id": "chunk-2",
            "content": "图片说明",
            "content_type": "image",
            "doc_id": "doc-1",
            "section": "附图",
            "source_document": "标准规范/a.pdf",
        },
    ]
    module = SimpleNamespace(
        kg_builder=SimpleNamespace(db=_FakeDB(docs))
    )

    chunks = data_process_api._load_chunks_from_builder_db(module, doc_ids=None)

    assert chunks == [
        {
            "_id": "mongo-1",
            "chunk_id": "chunk-1",
            "content": "门诊部应靠近医院主入口，方便患者到达。",
            "content_type": "text",
            "doc_id": "doc-1",
            "section": "总则",
            "source_document": "标准规范/a.pdf",
        }
    ]


def test_find_latest_resumable_kg_payload_prefers_latest_matching_resume_artifacts():
    old_payload = {
        "kind": "kg_build",
        "strategy": "B3",
        "build_signature": "sig-1",
        "resume_from_stage": "triplet_optimization",
        "ea_pairs": [{"entity_name": "旧门诊部", "entity_type": "功能分区"}],
        "triplets": [{"subject": "旧门诊部", "relation": "包含", "object": "旧挂号区"}],
    }
    latest_payload = {
        "kind": "kg_build",
        "strategy": "B3",
        "build_signature": "sig-1",
        "resume_from_stage": "cross_document_fusion",
        "ea_pairs": [{"entity_name": "门诊部", "entity_type": "功能分区"}],
        "triplets": [{"subject": "门诊部", "relation": "包含", "object": "挂号区"}],
    }

    tasks = {
        "older": {
            "module": "kg",
            "status": data_process_api.TaskStatus.FAILED,
            "created_at": "2026-03-26T03:00:00",
            "resume_payload": old_payload,
        },
        "newer": {
            "module": "kg",
            "status": data_process_api.TaskStatus.FAILED,
            "created_at": "2026-03-26T04:00:00",
            "resume_payload": latest_payload,
        },
        "mismatch": {
            "module": "kg",
            "status": data_process_api.TaskStatus.FAILED,
            "created_at": "2026-03-26T05:00:00",
            "resume_payload": {
                "kind": "kg_build",
                "strategy": "B1",
                "build_signature": "sig-1",
                "resume_from_stage": "triplet_optimization",
                "ea_pairs": [{"entity_name": "不匹配", "entity_type": "功能分区"}],
                "triplets": [{"subject": "不匹配", "relation": "包含", "object": "不匹配"}],
            },
        },
    }

    payload = data_process_api._find_latest_resumable_kg_payload(
        tasks,
        strategy="B3",
        build_signature="sig-1",
    )

    assert payload == latest_payload


def test_find_latest_resumable_kg_payload_requires_complete_artifacts():
    tasks = {
        "incomplete": {
            "module": "kg",
            "status": data_process_api.TaskStatus.FAILED,
            "created_at": "2026-03-26T04:00:00",
            "resume_payload": {
                "kind": "kg_build",
                "strategy": "B3",
                "build_signature": "sig-1",
                "resume_from_stage": "triplet_optimization",
                "ea_pairs": [{"entity_name": "门诊部", "entity_type": "功能分区"}],
            },
        }
    }

    payload = data_process_api._find_latest_resumable_kg_payload(
        tasks,
        strategy="B3",
        build_signature="sig-1",
    )

    assert payload is None
