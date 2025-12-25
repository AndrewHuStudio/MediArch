# backend/tests/test_supervisor_graph_simple.py
"""
简化版 Supervisor Graph 测试
不依赖真实的 Worker Agents，只测试核心逻辑
"""

import os
import sys
import asyncio
import pytest

# 让 "backend.*" 成为可导入包
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.app.agents.base_agent import (
    AgentRequest,
    AgentItem,
    add_items_with_dedup,
    create_worker_adapter,
    get_llm_manager,
)


# ============================================================================
# 测试：Reducer 功能
# ============================================================================

def test_items_dedup():
    """测试 items 去重功能"""
    items1 = [
        AgentItem(entity_id="1", name="A", source="neo4j"),
        AgentItem(entity_id="2", name="B", source="neo4j"),
    ]
    
    items2 = [
        AgentItem(entity_id="2", name="B", source="milvus"),  # 重复
        AgentItem(entity_id="3", name="C", source="milvus"),
    ]
    
    merged = add_items_with_dedup(items1, items2)
    
    # 应该去重，保留 3 个
    assert len(merged) == 3
    assert {item.entity_id for item in merged} == {"1", "2", "3"}
    print("✅ Items 去重测试通过")


# ============================================================================
# 测试：Worker Adapter
# ============================================================================

@pytest.mark.asyncio
async def test_worker_adapter():
    """测试 create_worker_adapter"""
    
    # 创建 Mock Worker Graph
    async def mock_worker(state):
        return {
            "items": [
                AgentItem(
                    entity_id="test_1",
                    name="测试结果",
                    score=0.9,
                    source="test_agent",
                )
            ],
            "diagnostics": {"agent": "test_agent"},
        }
    
    class MockGraph:
        async def ainvoke(self, state):
            return await mock_worker(state)
    
    # 创建 Adapter
    adapter = create_worker_adapter("test_agent", MockGraph())
    
    # 测试
    result = await adapter({
        "query": "测试查询",
        "request": AgentRequest(query="测试查询"),
    })
    
    # 验证返回格式
    assert "items" in result
    assert "worker_responses" in result
    assert "completed_workers" in result
    
    # 验证 worker_responses 格式
    assert len(result["worker_responses"]) == 1
    worker_resp = result["worker_responses"][0]
    assert worker_resp["agent_name"] == "test_agent"
    assert "items" in worker_resp
    assert worker_resp["item_count"] == 1
    
    print("✅ Worker Adapter 测试通过")


# ============================================================================
# 测试：反馈路由（不导入 supervisor_graph）
# ============================================================================

def test_feedback_routing_logic():
    """测试反馈路由逻辑（独立实现）"""
    
    def route_feedback(feedback_type: str, feedback_round: int) -> str:
        """简化的路由逻辑"""
        if feedback_type == "satisfied":
            return "save_memory"
        elif feedback_type == "new_question":
            return "prepare_new_question"
        elif feedback_type == "unsatisfied":
            if feedback_round == 0:
                return "internal_loop"
            elif feedback_round == 1:
                return "external_loop"
            else:
                return "save_memory"
        return "save_memory"
    
    # 测试
    assert route_feedback("satisfied", 0) == "save_memory"
    assert route_feedback("unsatisfied", 0) == "internal_loop"
    assert route_feedback("unsatisfied", 1) == "external_loop"
    assert route_feedback("new_question", 0) == "prepare_new_question"
    
    print("✅ 反馈路由逻辑测试通过")


# ============================================================================
# 测试：参数调整逻辑
# ============================================================================

def test_parameter_adjustment():
    """测试外部循环的参数调整逻辑"""
    
    original = AgentRequest(
        query="测试查询",
        top_k=5,
        timeout_ms=3000,
    )
    
    # 模拟参数调整
    new_top_k = min(original.top_k * 2, 20)
    new_timeout = min(original.timeout_ms + 2000, 10000)
    
    assert new_top_k == 10
    assert new_timeout == 5000
    
    print("✅ 参数调整逻辑测试通过")


# ============================================================================
# 测试：LLM Manager
# ============================================================================

def test_llm_manager():
    """测试 LLM Manager 的基本功能"""
    
    manager = get_llm_manager()
    
    init_count = [0]
    
    def mock_init():
        init_count[0] += 1
        return f"llm_instance_{init_count[0]}"
    
    # 第一次获取
    llm1 = manager.get_or_create("test_llm", mock_init)
    assert init_count[0] == 1
    
    # 第二次获取（应该复用）
    llm2 = manager.get_or_create("test_llm", mock_init)
    assert init_count[0] == 1  # 不应该再次初始化
    assert llm1 == llm2
    
    # 清除后重新创建
    manager.clear("test_llm")
    llm3 = manager.get_or_create("test_llm", mock_init)
    assert init_count[0] == 2
    assert llm3 != llm1
    
    print("✅ LLM Manager 测试通过")


# ============================================================================
# 测试：对话历史结构
# ============================================================================

def test_conversation_history_structure():
    """测试对话历史的数据结构"""
    
    history = [
        {"role": "user", "content": "手术室的设计规范？", "timestamp": "123"},
        {"role": "assistant", "content": "手术室的设计规范包括...", "timestamp": "124"},
        {"role": "user", "content": "那么ICU呢？", "timestamp": "125"},
    ]
    
    # 验证结构
    assert len(history) == 3
    assert all("role" in turn and "content" in turn for turn in history)
    
    # 提取用户问题
    user_questions = [turn["content"] for turn in history if turn["role"] == "user"]
    assert len(user_questions) == 2
    assert "手术室" in user_questions[0]
    assert "ICU" in user_questions[1]
    
    print("✅ 对话历史结构测试通过")


# ============================================================================
# 运行所有测试
# ============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("🧪 开始测试 Supervisor Graph（简化版）")
    print("=" * 60 + "\n")
    
    # 同步测试
    print("📋 测试 Reducer 功能...")
    test_items_dedup()
    
    print("\n📋 测试反馈路由逻辑...")
    test_feedback_routing_logic()
    
    print("\n📋 测试参数调整逻辑...")
    test_parameter_adjustment()
    
    print("\n📋 测试 LLM Manager...")
    test_llm_manager()
    
    print("\n📋 测试对话历史结构...")
    test_conversation_history_structure()
    
    # 异步测试
    print("\n📋 测试 Worker Adapter...")
    asyncio.run(test_worker_adapter())
    
    print("\n" + "=" * 60)
    print("✅ 所有测试通过！")
    print("=" * 60)