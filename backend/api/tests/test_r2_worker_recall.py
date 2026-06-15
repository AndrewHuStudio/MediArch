"""worker_recall 诊断构建测试：从 worker_responses 提取各 worker 召回的 source 列表。

用于链路取证（召回 vs 传递丢失）：probe_r2_passing_trace 读最终响应里的
diagnostics.additional_info.worker_recall，判定金标准是召回阶段缺失还是传递阶段丢失。
"""

from backend.api.routers.chat import build_worker_recall


class _Item:
    def __init__(self, citations):
        self.citations = citations


def test_build_worker_recall_groups_sources_by_agent():
    responses = [
        {
            "agent_name": "mongodb_agent",
            "items": [
                _Item([{"source": "GB 51039-2014.pdf"}]),
                _Item([{"source": "noise.pdf"}]),
            ],
        },
        {
            "agent_name": "milvus_agent",
            "items": [_Item([{"source": "GB 51039-2014.pdf"}])],
        },
    ]
    recall = build_worker_recall(responses)
    assert recall["mongodb_agent"] == ["GB 51039-2014.pdf", "noise.pdf"]
    assert recall["milvus_agent"] == ["GB 51039-2014.pdf"]


def test_build_worker_recall_dedupes_within_agent_and_skips_empty():
    responses = [
        {
            "agent_name": "mongodb_agent",
            "items": [
                _Item([{"source": "A.pdf"}, {"source": "A.pdf"}]),
                _Item([{"source": ""}]),
                _Item([{}]),
            ],
        }
    ]
    recall = build_worker_recall(responses)
    assert recall["mongodb_agent"] == ["A.pdf"]


def test_build_worker_recall_handles_dict_items_and_missing_fields():
    responses = [
        {"agent_name": "mongodb_agent", "items": [{"citations": [{"source": "B.pdf"}]}]},
        {"agent_name": "neo4j_agent"},  # 无 items
    ]
    recall = build_worker_recall(responses)
    assert recall["mongodb_agent"] == ["B.pdf"]
    assert "neo4j_agent" not in recall  # 无召回 source 不建空键
