from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.agents.result_synthesizer_agent.agent import build_synthesizer_graph


def test_synthesizer_graph_includes_quality_loop_nodes():
    graph = build_synthesizer_graph().get_graph()

    assert {"evaluate_quality", "request_retry", "finalize", "finalize_with_warning"} <= set(graph.nodes)


def test_synthesizer_graph_includes_retry_and_finalize_edges():
    graph = build_synthesizer_graph().get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert ("synthesize", "evaluate_quality") in edges
    assert ("request_retry", "synthesize") in edges
    assert ("finalize", "__end__") in edges
    assert ("finalize_with_warning", "__end__") in edges
