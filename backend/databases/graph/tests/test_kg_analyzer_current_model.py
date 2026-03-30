import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.databases.graph.utils.kg_analyzer import KGAnalyzer


class _FakeMongoClient:
    def close(self):
        return None


class _FakeResult:
    def __init__(self, records):
        self.records = records

    def single(self):
        if not self.records:
            return {}
        return self.records[0]

    def __iter__(self):
        return iter(self.records)


class _FakeSession:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def run(self, query, **kwargs):
        self.calls.append({"query": query, "kwargs": kwargs})

        stale_patterns = (":Entity", ":Attribute", ":RELATION", "r.type as relation")
        for pattern in stale_patterns:
            if pattern in query:
                raise AssertionError(f"stale graph-model query detected: {pattern}")

        if "MATCH (n) RETURN count(n) as total" in query:
            return _FakeResult([{"total": 6}])
        if "coalesce(n.level, 2) = 2" in query and "count(n) as total" in query:
            return _FakeResult([{"total": 5}])
        if "coalesce(n.level, 2) = 1" in query and "count(n) as total" in query:
            return _FakeResult([{"total": 1}])
        if "MATCH ()-[r]->() RETURN count(r) as total" in query:
            return _FakeResult([{"total": 4}])
        if "WITH a.id as s, type(r) as t, b.id as o, count(*) as c" in query:
            return _FakeResult([{"dup_triplets": 0}])
        if "RETURN count(r) as dangling" in query:
            return _FakeResult([{"dangling": 0}])
        if "RETURN n.schema_type as type, count(*) as count" in query:
            return _FakeResult([{"type": "空间", "count": 2}])
        if "RETURN type(r) as rel_type, count(*) as count" in query:
            return _FakeResult([{"rel_type": "MENTIONED_IN", "count": 2}])
        if "min(degree) as min_degree" in query:
            return _FakeResult([{"min_degree": 1, "avg_degree": 1.5, "max_degree": 3}])
        if "WHERE (coalesce(n.name, '') CONTAINS $keyword" in query:
            return _FakeResult([{"name": "重症监护室", "level": 2, "type": "空间"}])
        if "RETURN coalesce(n.name, n.title, n.id) as name" in query and "degree" in query:
            return _FakeResult([{"name": "重症监护室", "type": "空间", "degree": 3}])
        if "RETURN coalesce(a.name, a.title, a.id) as subject" in query:
            return _FakeResult(
                [{"subject": "重症监护室", "relation": "CONNECTED_TO", "object": "护士站"}]
            )

        raise AssertionError(f"unexpected query: {query}")


class _FakeDriver:
    def __init__(self, calls):
        self.calls = calls

    def session(self):
        return _FakeSession(self.calls)


def test_kg_analyzer_uses_current_graph_model_queries():
    calls = []
    analyzer = KGAnalyzer.__new__(KGAnalyzer)
    analyzer.neo4j_driver = _FakeDriver(calls)
    analyzer.mongo_client = _FakeMongoClient()

    stats = analyzer.analyze_neo4j()
    analyzer.show_sample_triples(limit=5)
    analyzer.search_entity("重症")

    assert stats["entity_nodes"] == 5
    assert stats["attribute_nodes"] == 1
    assert stats["total_relationships"] == 4
    assert any("coalesce(n.level, 2) = 2" in call["query"] for call in calls)
    assert any(
        "RETURN coalesce(a.name, a.title, a.id) as subject" in call["query"]
        for call in calls
    )
    assert any(
        "WHERE (coalesce(n.name, '') CONTAINS $keyword" in call["query"]
        for call in calls
    )


def test_evaluate_normalcy_accepts_embedded_attribute_model(capsys):
    analyzer = KGAnalyzer.__new__(KGAnalyzer)
    analyzer.neo4j_driver = None
    analyzer.mongo_client = _FakeMongoClient()

    analyzer.evaluate_normalcy(
        mongodb_stats={"doc_count": 2, "total_chunks": 10},
        neo4j_stats={
            "total_nodes": 48,
            "entity_nodes": 40,
            "attribute_nodes": 0,
            "total_relationships": 24,
        },
    )

    output = capsys.readouterr().out
    assert "属性已内嵌" in output
    assert "平均每个实体只有0.0个属性" not in output
