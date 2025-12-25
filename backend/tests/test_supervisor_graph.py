# backend/tests/test_supervisor_graph.py

import os
import sys
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# 让 "backend.*" 成为可导入包：把【项目根目录】加入 sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.app.agents.base_agent import (
    AgentRequest,
    AgentItem,
)


# ============================================================================
# Mock Worker Graphs（避免依赖真实的 Worker）
# ============================================================================

def create_mock_worker_graph(agent_name: str):
    """创建 Mock Worker Graph"""
    
    async def mock_worker(state):
        """Mock Worker 的行为"""
        query = state.get("query", "")
        
        # 返回模拟数据
        return {
            "items": [
                AgentItem(
                    entity_id=f"{agent_name}_item_1",
                    name=f"{agent_name} 结果1",
                    score=0.9,
                    source=agent_name,
                )
            ],
            "diagnostics": {
                "agent": agent_name,
                "result_count": 1,
            }
        }
    
    # 创建 Mock Graph
    mock_graph = MagicMock()
    mock_graph.ainvoke = mock_worker
    
    return mock_graph


# ============================================================================
# 测试：基本查询流程
# ============================================================================

@pytest.mark.asyncio
async def test_supervisor_basic_query():
    """测试基本查询流程（不使用真实 Workers）"""
    
    # Mock Worker Graphs
    with patch('backend.app.agents.supervisor_graph._get_worker_workflows') as mock_get_workers:
        mock_get_workers.return_value = {
            "neo4j_agent": create_mock_worker_graph("neo4j_agent"),
            "milvus_agent": create_mock_worker_graph("milvus_agent"),
        }
        
        # Mock Orchestrator
        with patch('backend.app.agents.supervisor_graph.orchestrator_logic_graph') as mock_orch:
            async def mock_orchestrator(state):
                return {
                    "is_hospital_related": True,
                    "agents_to_call": ["neo4j_agent", "milvus_agent"],
                    "rewritten_query": state.get("query", ""),
                }
            
            mock_orch.ainvoke = mock_orchestrator
            
            # Mock Synthesizer
            with patch('backend.app.agents.supervisor_graph.synth_graph') as mock_synth:
                async def mock_synthesizer(state):
                    return {
                        "final_answer": "这是手术室的设计规范...",
                        "recommended_questions": ["关于 ICU 的设计规范？"],
                        "quality_score": 0.9,
                    }
                
                mock_synth.ainvoke = mock_synthesizer
                
                # 重新构建图
                from backend.app.agents.supervisor_graph import build_supervisor_graph
                graph = build_supervisor_graph()
                
                # 执行查询
                result = await graph.ainvoke({
                    "query": "手术室的设计规范？",
                    "user_id": "test_user",
                    "session_id": "test_session",
                    "request": AgentRequest(query="手术室的设计规范？"),
                })
                
                # 验证结果
                assert "final_answer" in result
                assert result["final_answer"]
                print("✅ 基本查询测试通过")


# ============================================================================
# 测试：Reducer 功能
# ============================================================================

def test_supervisor_items_dedup():
    """测试 items 去重功能"""
    from backend.app.agents.base_agent import add_items_with_dedup
    
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
async def test_create_worker_adapter():
    """测试 create_worker_adapter 函数"""
    from backend.app.agents.base_agent import create_worker_adapter
    
    # 创建 Mock Worker Graph
    mock_worker = create_mock_worker_graph("test_agent")
    
    # 创建 Adapter
    adapter = create_worker_adapter("test_agent", mock_worker)
    
    # 测试 Adapter
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
    assert "diagnostics" in worker_resp
    assert "item_count" in worker_resp
    
    print("✅ Worker Adapter 测试通过")


# ============================================================================
# 测试：Human-in-the-Loop 反馈分类
# ============================================================================

@pytest.mark.asyncio
async def test_classify_feedback():
    """测试用户反馈分类"""
    
    # Mock LLM
    with patch('backend.app.agents.supervisor_graph.get_llm_manager') as mock_manager:
        mock_llm = AsyncMock()
        
        # Mock 分类结果
        async def mock_classify(messages):
            mock_response = MagicMock()
            mock_response.content = '{"type": "unsatisfied", "reason": "不够详细"}'
            return mock_response
        
        mock_llm.ainvoke = mock_classify
        mock_manager.return_value.get_or_create.return_value = mock_llm
        
        # 测试分类节点
        from backend.app.agents.supervisor_graph import node_classify_feedback
        
        result = await node_classify_feedback({
            "user_feedback_raw": "不够详细，能再详细点吗？"
        })
        
        # 验证分类结果
        assert result["user_feedback_type"] == "unsatisfied"
        print("✅ 反馈分类测试通过")


# ============================================================================
# 测试：反馈路由
# ============================================================================

def test_route_after_feedback():
    """测试反馈后的路由决策"""
    from backend.app.agents.supervisor_graph import route_after_feedback
    
    # 测试：满意
    route = route_after_feedback({
        "user_feedback_type": "satisfied",
        "feedback_round": 0,
    })
    assert route == "save_memory"
    
    # 测试：首次不满意（内部循环）
    route = route_after_feedback({
        "user_feedback_type": "unsatisfied",
        "feedback_round": 0,
    })
    assert route == "internal_loop"
    
    # 测试：第二次不满意（外部循环）
    route = route_after_feedback({
        "user_feedback_type": "unsatisfied",
        "feedback_round": 1,
    })
    assert route == "external_loop"
    
    # 测试：新问题
    route = route_after_feedback({
        "user_feedback_type": "new_question",
        "feedback_round": 0,
    })
    assert route == "prepare_new_question"
    
    print("✅ 反馈路由测试通过")


# ============================================================================
# 测试：对话历史管理
# ============================================================================

def test_conversation_history():
    """测试对话历史的保存和读取"""
    from backend.app.agents.supervisor_graph import (
        save_conversation_turn,
        get_conversation_history
    )
    
    user_id = "test_user"
    session_id = "test_session"
    
    # 保存对话
    save_conversation_turn(user_id, session_id, "user", "手术室的设计规范？")
    save_conversation_turn(user_id, session_id, "assistant", "手术室的设计规范包括...")
    save_conversation_turn(user_id, session_id, "user", "那么ICU呢？")
    
    # 读取历史
    history = get_conversation_history(user_id, session_id, limit=10)
    
    # 验证
    assert len(history) == 3
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "手术室的设计规范？"
    assert history[1]["role"] == "assistant"
    assert history[2]["role"] == "user"
    
    print("✅ 对话历史测试通过")


# ============================================================================
# 测试：外部循环参数调整
# ============================================================================

def test_prepare_retry_with_adjusted_params():
    """测试外部循环的参数调整"""
    from backend.app.agents.supervisor_graph import node_prepare_retry_with_adjusted_params
    
    original_request = AgentRequest(
        query="手术室的面积要求？",
        top_k=5,
        timeout_ms=3000,
    )
    
    result = node_prepare_retry_with_adjusted_params({
        "request": original_request,
        "feedback_round": 1,
    })
    
    # 验证参数调整
    adjusted_request = result["request"]
    assert adjusted_request.top_k == 10  # 5 * 2
    assert adjusted_request.timeout_ms == 5000  # 3000 + 2000
    assert adjusted_request.metadata.get("retry_mode") == "external_loop"
    
    print("✅ 参数调整测试通过")


# ============================================================================
# 测试：新问题的历史保留
# ============================================================================

def test_prepare_new_question_with_history():
    """测试补充提问时保留对话历史"""
    from backend.app.agents.supervisor_graph import node_prepare_new_question_with_history
    
    # 准备历史
    conversation_history = [
        {"role": "user", "content": "手术室的面积要求？"},
        {"role": "assistant", "content": "手术室的面积要求是..."},
    ]
    
    result = node_prepare_new_question_with_history({
        "user_feedback_raw": "那么ICU呢？",
        "conversation_history": conversation_history,
        "request": AgentRequest(query="手术室的面积要求？"),
    })
    
    # 验证
    assert result["query"] == "那么ICU呢？"
    assert result["conversation_history"] == conversation_history
    assert result["feedback_round"] == 0  # 重置
    
    # 验证 context 包含历史
    new_request = result["request"]
    assert len(new_request.context) > 0
    assert "手术室" in new_request.context[0]
    
    print("✅ 新问题历史保留测试通过")


# ============================================================================
# 运行所有测试
# ============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("🧪 开始测试 Supervisor Graph")
    print("=" * 60 + "\n")
    
    # 同步测试
    print("📋 测试 Reducer 功能...")
    test_supervisor_items_dedup()
    
    print("\n📋 测试反馈路由...")
    test_route_after_feedback()
    
    print("\n📋 测试对话历史...")
    test_conversation_history()
    
    print("\n📋 测试参数调整...")
    test_prepare_retry_with_adjusted_params()
    
    print("\n📋 测试新问题历史保留...")
    test_prepare_new_question_with_history()
    
    # 异步测试
    print("\n📋 测试 Worker Adapter...")
    asyncio.run(test_create_worker_adapter())
    
    print("\n📋 测试反馈分类...")
    asyncio.run(test_classify_feedback())
    
    print("\n" + "=" * 60)
    print("✅ 所有测试通过！")
    print("=" * 60)