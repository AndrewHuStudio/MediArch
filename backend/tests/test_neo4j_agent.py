# backend/tests/test_neo4j_agent.py

import os
import sys
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ============================================================================
# 测试：关键词提取
# ============================================================================

def test_extract_keywords():
    """测试关键词提取"""
    from backend.app.agents.neo4j_agent.agent import extract_keywords
    
    # 测试1：领域词
    keywords = extract_keywords("手术室的设计要点？")
    assert "手术室" in keywords
    print(f"✅ 提取关键词: {keywords}")
    
    # 测试2：多个领域词
    keywords = extract_keywords("门诊部和住院部的关系？")
    assert "门诊部" in keywords
    assert "住院部" in keywords
    print(f"✅ 提取关键词: {keywords}")


# ============================================================================
# 测试：去重
# ============================================================================

def test_deduplicate_terms():
    """测试去重"""
    from backend.app.agents.neo4j_agent.agent import deduplicate_terms
    
    terms = ["手术室", "ICU", "手术室", "门诊", "手术室"]
    result = deduplicate_terms(terms)
    
    assert len(result) == 3
    assert result == ["手术室", "ICU", "门诊"]
    print("✅ 去重测试通过")


# ============================================================================
# 测试：质量计算
# ============================================================================

def test_calculate_quality():
    """测试质量分数计算"""
    from backend.app.agents.neo4j_agent.agent import calculate_quality
    from backend.app.agents.base_agent import AgentItem
    
    items = [
        AgentItem(entity_id="1", score=0.9, citations=[{"source": "spec1"}]),
        AgentItem(entity_id="2", score=0.8, citations=[{"source": "spec2"}]),
    ]
    
    score = calculate_quality(items, "手术室设计")
    assert 0 <= score <= 1
    print(f"✅ 质量分数: {score:.2f}")


# ============================================================================
# 测试：启发式分析
# ============================================================================

def test_heuristic_query_analysis():
    """测试启发式查询分析"""
    from backend.app.agents.neo4j_agent.agent import heuristic_query_analysis
    
    # 测试1：实体查询
    result = heuristic_query_analysis("手术室的设计要点？")
    assert result["query_type"] in ["entity", "relation", "community", "mixed"]
    print(f"✅ 启发式分析: {result}")
    
    # 测试2：关系查询
    result = heuristic_query_analysis("门诊部和住院部的关系？")
    assert result["query_type"] == "relation"
    print(f"✅ 启发式分析: {result}")


# ============================================================================
# 测试：节点函数（Mock Retriever）
# ============================================================================

async def test_node_query_analysis():
    """测试查询分析节点"""
    # Mock LLM
    with patch('backend.app.agents.neo4j_agent.agent.get_analysis_llm') as mock_llm_fn:
        mock_llm = AsyncMock()
        mock_llm_fn.return_value = mock_llm
        
        # Mock LLM 失败，使用启发式
        mock_llm.ainvoke.side_effect = Exception("LLM failed")
        
        from backend.app.agents.neo4j_agent.agent import node_query_analysis
        
        result = await node_query_analysis({
            "query": "手术室的设计要点？"
        })
        
        assert "query_type" in result
        assert "search_terms" in result
        print(f"✅ 查询分析: {result}")


async def test_node_merge_results():
    """测试结果融合节点"""
    from backend.app.agents.neo4j_agent.agent import node_merge_results
    from backend.app.agents.base_agent import AgentItem
    
    state = {
        "query_type": "entity",
        "entity_results": [
            AgentItem(entity_id="1", name="手术室", score=0.9),
            AgentItem(entity_id="2", name="ICU", score=0.8),
        ],
        "relation_results": [
            AgentItem(entity_id="3", name="功能关系", score=0.7),
        ],
        "query": "手术室设计",
    }
    
    result = node_merge_results(state)
    
    assert "items" in result
    assert "merged_items" in result
    assert "quality_score" in result
    assert len(result["items"]) > 0
    print(f"✅ 融合结果: {len(result['items'])} 项, 质量={result['quality_score']:.2f}")


# ============================================================================
# 测试：图构建
# ============================================================================

def test_graph_structure():
    """测试图结构"""
    from backend.app.agents.neo4j_agent.agent import graph
    
    assert graph is not None
    assert hasattr(graph, 'ainvoke')
    print("✅ 图结构正确")


# ============================================================================
# 测试：LLM Manager
# ============================================================================

def test_llm_manager_usage():
    """测试 LLM Manager 使用"""
    from backend.app.agents.base_agent import get_llm_manager
    
    manager = get_llm_manager()
    
    init_count = [0]
    
    def mock_init():
        init_count[0] += 1
        return MagicMock()
    
    # 第一次获取
    llm1 = manager.get_or_create("test_neo4j", mock_init)
    assert init_count[0] == 1
    
    # 第二次获取（复用）
    llm2 = manager.get_or_create("test_neo4j", mock_init)
    assert init_count[0] == 1
    assert llm1 is llm2
    
    print("✅ LLM Manager 使用正确")


# ============================================================================
# 运行所有测试
# ============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("🧪 开始测试 Neo4j Agent")
    print("=" * 60 + "\n")
    
    # 同步测试
    print("📋 测试关键词提取...")
    test_extract_keywords()
    
    print("\n📋 测试去重...")
    test_deduplicate_terms()
    
    print("\n📋 测试质量计算...")
    test_calculate_quality()
    
    print("\n📋 测试启发式分析...")
    test_heuristic_query_analysis()
    
    print("\n📋 测试图结构...")
    test_graph_structure()
    
    print("\n📋 测试 LLM Manager...")
    test_llm_manager_usage()
    
    # 异步测试
    print("\n📋 测试查询分析节点...")
    asyncio.run(test_node_query_analysis())
    
    print("\n📋 测试结果融合节点...")
    asyncio.run(test_node_merge_results())
    
    print("\n" + "=" * 60)
    print("✅ 所有测试通过！")
    print("=" * 60)