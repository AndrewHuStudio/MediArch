# backend/tests/test_result_synthesizer.py

import os
import sys
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# 让 "backend.*" 成为可导入包：把【项目根目录】加入 sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.app.agents.base_agent import AgentItem


# ============================================================================
# 测试：Aggregate 节点
# ============================================================================

def test_node_aggregate():
    """测试聚合多个 Worker 响应"""
    from backend.app.agents.result_synthesizer_agent.agent import node_aggregate
    
    # 准备 worker_responses
    worker_responses = [
        {
            "agent_name": "neo4j_agent",
            "items": [
                AgentItem(entity_id="1", name="结果1", score=0.9, source="neo4j_agent"),
                AgentItem(entity_id="2", name="结果2", score=0.8, source="neo4j_agent"),
            ],
            "diagnostics": {},
            "item_count": 2,
        },
        {
            "agent_name": "milvus_agent",
            "items": [
                AgentItem(entity_id="2", name="结果2", score=0.85, source="milvus_agent"),  # 重复
                AgentItem(entity_id="3", name="结果3", score=0.7, source="milvus_agent"),
            ],
            "diagnostics": {},
            "item_count": 2,
        }
    ]
    
    # 执行聚合
    result = node_aggregate({"worker_responses": worker_responses})
    
    # 验证去重
    assert len(result["aggregated_items"]) == 3  # 去重后应该是 3 个
    entity_ids = {item.entity_id for item in result["aggregated_items"]}
    assert entity_ids == {"1", "2", "3"}
    
    # 验证排序（按 score 降序）
    scores = [item.score for item in result["aggregated_items"]]
    assert scores == sorted(scores, reverse=True)
    
    print("✅ Aggregate 节点测试通过")


# ============================================================================
# 测试：Synthesize 节点（使用 feedback）
# ============================================================================

@pytest.mark.asyncio
async def test_node_synthesize_with_feedback():
    """测试合成节点使用反馈"""
    
    # Mock LLM
    with patch('backend.app.agents.result_synthesizer_agent.agent.get_llm_manager') as mock_manager:
        mock_llm = AsyncMock()
        
        # Mock LLM 响应
        async def mock_llm_invoke(messages):
            mock_response = MagicMock()
            
            # 检查是否包含反馈
            system_msg = messages[0].content
            if "改进要求" in system_msg:
                mock_response.content = "这是改进后的答案，更加详细..."
            else:
                mock_response.content = "这是初始答案..."
            
            return mock_response
        
        mock_llm.ainvoke = mock_llm_invoke
        mock_manager.return_value.get_or_create.return_value = mock_llm
        
        from backend.app.agents.result_synthesizer_agent.agent import node_synthesize
        
        # 第一次合成（无反馈）
        result1 = await node_synthesize({
            "query": "手术室的设计规范？",
            "aggregated_items": [
                AgentItem(entity_id="1", name="结果1", score=0.9),
            ],
            "notes": [],
            "retry_count": 0,
            "feedback_message": "",
        })
        
        assert "初始答案" in result1["final_answer"]
        
        # 第二次合成（使用反馈）
        result2 = await node_synthesize({
            "query": "手术室的设计规范？",
            "aggregated_items": [
                AgentItem(entity_id="1", name="结果1", score=0.9),
            ],
            "notes": [],
            "retry_count": 1,
            "feedback_message": "需要更详细的面积要求",
        })
        
        assert "改进后的答案" in result2["final_answer"]
        print("✅ Synthesize 节点（使用反馈）测试通过")


# ============================================================================
# 测试：Evaluate Quality 节点
# ============================================================================

@pytest.mark.asyncio
async def test_node_evaluate_quality():
    """测试质量评估节点"""
    
    # Mock LLM
    with patch('backend.app.agents.result_synthesizer_agent.agent.get_llm_manager') as mock_manager:
        mock_llm = AsyncMock()
        
        # Mock 评估结果
        async def mock_evaluate(messages):
            mock_response = MagicMock()
            mock_response.content = '''
            {
              "quality_score": 0.85,
              "is_quality_good": true,
              "feedback": ""
            }
            '''
            return mock_response
        
        mock_llm.ainvoke = mock_evaluate
        mock_manager.return_value.get_or_create.return_value = mock_llm
        
        from backend.app.agents.result_synthesizer_agent.agent import node_evaluate_quality
        
        result = await node_evaluate_quality({
            "query": "手术室的设计规范？",
            "final_answer": "手术室的设计规范包括...",
            "aggregated_items": [
                AgentItem(entity_id="1", name="结果1", citations=[{"source": "规范"}]),
                AgentItem(entity_id="2", name="结果2", citations=[{"source": "论文"}]),
            ],
            "retry_count": 0,
        })
        
        # 验证评估结果
        assert result["quality_score"] == 0.85
        assert result["is_quality_good"] is True
        assert result["feedback_message"] == ""
        
        print("✅ Evaluate Quality 节点测试通过")


# ============================================================================
# 测试：Route After Evaluation
# ============================================================================

def test_route_after_evaluation():
    """测试评估后的路由决策"""
    from backend.app.agents.result_synthesizer_agent.agent import route_after_evaluation
    
    # 测试：质量合格
    route = route_after_evaluation({
        "is_quality_good": True,
        "retry_count": 0,
    })
    assert route == "finalize"
    
    # 测试：质量不合格，首次重试
    route = route_after_evaluation({
        "is_quality_good": False,
        "retry_count": 0,
    })
    assert route == "request_retry"
    
    # 测试：质量不合格，第二次重试
    route = route_after_evaluation({
        "is_quality_good": False,
        "retry_count": 1,
    })
    assert route == "request_retry"
    
    # 测试：质量不合格，达到重试上限
    route = route_after_evaluation({
        "is_quality_good": False,
        "retry_count": 2,
    })
    assert route == "finalize_with_warning"
    
    print("✅ Route After Evaluation 测试通过")


# ============================================================================
# 测试：Request Retry 节点
# ============================================================================

def test_node_request_retry():
    """测试重试节点"""
    from backend.app.agents.result_synthesizer_agent.agent import node_request_retry
    
    result = node_request_retry({
        "retry_count": 0,
        "feedback_message": "需要更详细的信息",
    })
    
    # 验证重试计数增加
    assert result["retry_count"] == 1
    assert result["feedback_message"] == "需要更详细的信息"
    
    print("✅ Request Retry 节点测试通过")


# ============================================================================
# 测试：Finalize 节点
# ============================================================================

def test_node_finalize():
    """测试最终化节点"""
    from backend.app.agents.result_synthesizer_agent.agent import (
        node_finalize,
        node_finalize_with_warning
    )
    
    # 测试正常最终化
    result1 = node_finalize({
        "final_answer": "这是最终答案"
    })
    assert result1["final_answer"] == "这是最终答案"
    
    # 测试带警告的最终化
    result2 = node_finalize_with_warning({
        "final_answer": "这是最终答案"
    })
    assert "⚠️" in result2["final_answer"]
    assert "这是最终答案" in result2["final_answer"]
    
    print("✅ Finalize 节点测试通过")


# ============================================================================
# 测试：完整的反馈循环
# ============================================================================

@pytest.mark.asyncio
async def test_feedback_loop_integration():
    """测试完整的反馈循环（集成测试）"""
    
    # Mock LLM
    with patch('backend.app.agents.result_synthesizer_agent.agent.get_llm_manager') as mock_manager:
        mock_synthesizer_llm = AsyncMock()
        mock_evaluator_llm = AsyncMock()
        
        # Mock 合成 LLM
        synthesize_count = [0]
        
        async def mock_synthesize(messages):
            synthesize_count[0] += 1
            mock_response = MagicMock()
            mock_response.content = f"答案（第{synthesize_count[0]}次）"
            return mock_response
        
        mock_synthesizer_llm.ainvoke = mock_synthesize
        
        # Mock 评估 LLM
        eval_count = [0]
        
        async def mock_evaluate(messages):
            eval_count[0] += 1
            mock_response = MagicMock()
            
            # 第一次评估：不合格
            if eval_count[0] == 1:
                mock_response.content = '{"quality_score": 0.5, "is_quality_good": false, "feedback": "需要更详细"}'
            # 第二次评估：合格
            else:
                mock_response.content = '{"quality_score": 0.9, "is_quality_good": true, "feedback": ""}'
            
            return mock_response
        
        mock_evaluator_llm.ainvoke = mock_evaluate
        
        # 配置 LLM Manager
        def mock_get_or_create(name, init_func):
            if name == "synthesizer":
                return mock_synthesizer_llm
            elif name == "evaluator":
                return mock_evaluator_llm
            else:
                raise ValueError(f"Unknown LLM: {name}")
        
        mock_manager.return_value.get_or_create.side_effect = mock_get_or_create
        
        # 构建并执行图
        from backend.app.agents.result_synthesizer_agent.agent import build_synthesizer_graph
        
        graph = build_synthesizer_graph()
        
        result = await graph.ainvoke({
            "query": "手术室的设计规范？",
            "worker_responses": [
                {
                    "agent_name": "neo4j_agent",
                    "items": [
                        AgentItem(entity_id="1", name="结果1", score=0.9),
                    ],
                    "diagnostics": {},
                    "item_count": 1,
                }
            ],
        })
        
        # 验证反馈循环工作
        assert synthesize_count[0] == 2  # 应该合成了 2 次（初始 + 重试）
        assert eval_count[0] == 2  # 应该评估了 2 次
        assert "final_answer" in result
        
        print("✅ 反馈循环集成测试通过")


# ============================================================================
# 测试：LLM Manager 使用
# ============================================================================

@pytest.mark.asyncio
async def test_llm_manager_usage():
    """测试 LLM Manager 的使用"""
    from backend.app.agents.base_agent import get_llm_manager
    
    manager = get_llm_manager()
    
    # Mock 初始化函数
    init_count = [0]
    
    def mock_init():
        init_count[0] += 1
        return MagicMock()
    
    # 第一次获取（创建）
    llm1 = manager.get_or_create("test_synth", mock_init)
    assert init_count[0] == 1
    
    # 第二次获取（复用）
    llm2 = manager.get_or_create("test_synth", mock_init)
    assert init_count[0] == 1  # 不应该再次初始化
    assert llm1 is llm2  # 应该是同一个实例
    
    # 清除后重新创建
    manager.clear("test_synth")
    llm3 = manager.get_or_create("test_synth", mock_init)
    assert init_count[0] == 2
    assert llm3 is not llm1
    
    print("✅ LLM Manager 使用测试通过")


# ============================================================================
# 测试：规则兜底答案
# ============================================================================

def test_rule_based_answer():
    """测试规则兜底答案生成"""
    from backend.app.agents.result_synthesizer_agent.agent import _build_rule_based_answer
    
    items = [
        AgentItem(
            entity_id="1",
            name="手术室规范",
            score=0.9,
            source="neo4j_agent",
            snippet="手术室的设计规范包括...",
            citations=[{"source": "GB 50333-2013"}]
        ),
    ]
    
    result = _build_rule_based_answer(
        query="手术室的设计规范？",
        aggregated_items=items,
        notes=["检索成功"]
    )
    
    # 验证结果
    assert "final_answer" in result
    assert "手术室规范" in result["final_answer"]
    assert "recommended_questions" in result
    assert len(result["recommended_questions"]) > 0
    
    print("✅ 规则兜底答案测试通过")


# ============================================================================
# 运行所有测试
# ============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("🧪 开始测试 Result Synthesizer Agent")
    print("=" * 60 + "\n")
    
    # 同步测试
    print("📋 测试 Aggregate 节点...")
    test_node_aggregate()
    
    print("\n📋 测试 Route After Evaluation...")
    test_route_after_evaluation()
    
    print("\n📋 测试 Request Retry 节点...")
    test_node_request_retry()
    
    print("\n📋 测试 Finalize 节点...")
    test_node_finalize()
    
    print("\n📋 测试规则兜底答案...")
    test_rule_based_answer()
    
    # 异步测试
    print("\n📋 测试 Synthesize 节点（使用反馈）...")
    asyncio.run(test_node_synthesize_with_feedback())
    
    print("\n📋 测试 Evaluate Quality 节点...")
    asyncio.run(test_node_evaluate_quality())
    
    print("\n📋 测试 LLM Manager 使用...")
    asyncio.run(test_llm_manager_usage())
    
    print("\n📋 测试反馈循环集成...")
    asyncio.run(test_feedback_loop_integration())
    
    print("\n" + "=" * 60)
    print("✅ 所有测试通过！")
    print("=" * 60)