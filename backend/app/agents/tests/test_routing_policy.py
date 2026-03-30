from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.agents.routing_policy import select_workers_for_execution


def test_requested_workers_override_default_available_worker_fanout():
    available_workers = [
        "neo4j_agent",
        "milvus_agent",
        "mongodb_agent",
        "online_search_agent",
    ]

    selected = select_workers_for_execution(
        available_workers=available_workers,
        agents_to_call=["milvus_agent"],
    )

    assert selected == ["milvus_agent"]


def test_selected_workers_still_follow_global_priority_order():
    available_workers = [
        "neo4j_agent",
        "milvus_agent",
        "mongodb_agent",
        "online_search_agent",
    ]

    selected = select_workers_for_execution(
        available_workers=available_workers,
        agents_to_call=["mongodb_agent", "neo4j_agent"],
    )

    assert selected == ["neo4j_agent", "mongodb_agent"]


def test_strict_cross_doc_scope_filters_out_graph_and_online_search():
    available_workers = [
        "neo4j_agent",
        "milvus_agent",
        "mongodb_agent",
        "online_search_agent",
    ]

    selected = select_workers_for_execution(
        available_workers=available_workers,
        agents_to_call=[
            "neo4j_agent",
            "milvus_agent",
            "mongodb_agent",
            "online_search_agent",
        ],
        strict_cross_doc_request=True,
    )

    assert selected == ["milvus_agent", "mongodb_agent"]


def test_missing_orchestrator_selection_falls_back_to_available_workers():
    available_workers = ["neo4j_agent", "milvus_agent"]

    selected = select_workers_for_execution(
        available_workers=available_workers,
        agents_to_call=None,
    )

    assert selected == ["neo4j_agent", "milvus_agent"]
