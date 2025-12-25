# backend/tests/test_reducers.py

import os
import sys

# 关键修正：把“项目根目录”（backend 的上一级）加到 sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest
from backend.app.agents.base_agent import add_items_with_dedup, AgentItem


@pytest.mark.parametrize(
    "items1, items2, expected_count, expected_ids",
    [
        (
            [AgentItem(entity_id="1", name="A")],
            [AgentItem(entity_id="2", name="B"), AgentItem(entity_id="1", name="A")],
            2,
            ["1", "2"],
        ),
        ([], [AgentItem(entity_id="x", name="Only")], 1, ["x"]),
        ([AgentItem(entity_id="same", name="One")], [AgentItem(entity_id="same", name="One")], 1, ["same"]),
    ],
)
def test_add_items_with_dedup(items1, items2, expected_count, expected_ids):
    merged = add_items_with_dedup(items1, items2)
    assert len(merged) == expected_count
    assert [i.entity_id for i in merged] == expected_ids


if __name__ == "__main__":
    # 兼容直接运行
    test_add_items_with_dedup(
        [AgentItem(entity_id="1", name="A")],
        [AgentItem(entity_id="2", name="B"), AgentItem(entity_id="1", name="A")],
        2,
        ["1", "2"],
    )
    print("✅ Reducer 测试通过")
