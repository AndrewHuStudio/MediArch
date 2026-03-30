import asyncio
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.agents.base_agent import AgentRequest
from backend.app.agents.mediarch_graph import build_mediarch_graph


def test_mediarch_graph_contains_online_search_execution_path():
    graph = build_mediarch_graph().get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert "schedule_online_search" in graph.nodes
    assert ("schedule_mongodb", "schedule_online_search") in edges
    assert ("mongodb_agent", "schedule_online_search") in edges
    assert ("schedule_online_search", "online_search_agent") in edges
    assert ("schedule_online_search", "gather_responses") in edges
    assert ("online_search_agent", "gather_responses") in edges


class _FakeWorkerGraph:
    def __init__(self, agent_name: str):
        self.agent_name = agent_name

    async def ainvoke(self, worker_input):
        return {
            "items": [],
            "diagnostics": {
                "agent_name": self.agent_name,
                "query": worker_input.get("query"),
            },
        }


class _FakeFusionHints:
    entity_names = []
    entity_types = []
    chunk_ids = []
    sections = []
    page_ranges = []
    relations = []
    search_terms = []
    neo4j_entity_count = 0
    milvus_chunk_count = 0
    fusion_score = 0.0


class _FakeGraphData:
    def to_dict(self):
        return {"nodes": [], "links": []}


class _FakeFusionResult:
    unified_hints = _FakeFusionHints()
    graph_data = _FakeGraphData()
    merged_items = []
    diagnostics = {}


class _FakeCache:
    def get(self, *args, **kwargs):
        return None

    def set(self, *args, **kwargs):
        return None


def test_online_search_selected_by_orchestrator_still_executes_when_mongodb_is_skipped(monkeypatch):
    import backend.app.agents.mediarch_graph as mediarch_graph_module
    import backend.app.agents.result_synthesizer_agent.agent as synth_module

    async def fake_orchestrator(_state):
        return {
            "is_hospital_related": True,
            "agents_to_call": ["milvus_agent", "online_search_agent"],
        }

    async def fake_synthesizer(state):
        agent_names = [resp.get("agent_name") for resp in state.get("worker_responses", [])]
        return {
            "final_answer": ",".join(agent_names),
            "recommended_questions": [],
        }

    monkeypatch.setattr(mediarch_graph_module, "orchestrator_logic_graph", fake_orchestrator)
    monkeypatch.setattr(
        mediarch_graph_module,
        "_get_worker_workflows",
        lambda: {
            "milvus_agent": _FakeWorkerGraph("milvus_agent"),
            "online_search_agent": _FakeWorkerGraph("online_search_agent"),
        },
    )
    monkeypatch.setattr(mediarch_graph_module, "fuse_retrieval_results", lambda **kwargs: _FakeFusionResult())
    monkeypatch.setattr(mediarch_graph_module, "get_retrieval_cache", lambda: _FakeCache())
    monkeypatch.setattr(synth_module, "graph", fake_synthesizer)

    graph = mediarch_graph_module.build_mediarch_graph()
    request = AgentRequest(
        query="最新门诊大厅设计趋势",
        filters={},
        top_k=5,
        lang="zh",
        timeout_ms=1000,
        trace_id=None,
        metadata={},
        context=[],
        attachments=[],
    )

    result = asyncio.run(
        graph.ainvoke(
            {
                "request": request,
                "original_query": request.query,
            },
            config={"configurable": {"thread_id": "test-online-search"}},
        )
    )

    agent_names = [resp.get("agent_name") for resp in result.get("worker_responses", [])]

    assert "milvus_agent" in agent_names
    assert "online_search_agent" in agent_names
    assert result.get("scheduled_workers") == ["milvus_agent", "online_search_agent"]
