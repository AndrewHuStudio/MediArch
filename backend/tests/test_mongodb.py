#!/usr/bin/env python3
"""测试 MongoDB Agent"""

import os
import sys
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ============================================================================
# 测试：辅助函数
# ============================================================================

def test_deduplicate_terms():
    """测试去重"""
    from backend.app.agents.mongodb_agent.agent import deduplicate_terms
    
    terms = ["病房", "ICU", "病房", "门诊", "病房"]
    result = deduplicate_terms(terms)
    
    assert len(result) == 3
    assert result == ["病房", "ICU", "门诊"]
    print("✅ 去重测试通过")


def test_heuristic_rewrite():
    """测试启发式改写"""
    from backend.app.agents.mongodb_agent.agent import heuristic_rewrite
    
    result = heuristic_rewrite("病房的设计要点？")
    
    assert "search_terms" in result
    assert len(result["search_terms"]) > 0
    print(f"✅ 启发式改写: {result}")


# ============================================================================
# 测试：节点函数
# ============================================================================

def test_node_extract_query():
    """测试提取查询节点"""
    from backend.app.agents.mongodb_agent.agent import node_extract_query
    from backend.app.agents.base_agent import AgentRequest
    
    result = node_extract_query({
        "request": AgentRequest(query="病房的设计要点？")
    })
    
    assert result["query"] == "病房的设计要点？"
    print(f"✅ 提取查询: {result}")


async def test_node_rewrite_query():
    """测试查询改写节点"""
    # Mock LLM
    with patch('backend.app.agents.mongodb_agent.agent.get_rewrite_llm') as mock_llm_fn:
        mock_llm = AsyncMock()
        mock_llm_fn.return_value = mock_llm
        
        # Mock 失败，使用启发式
        mock_llm.ainvoke.side_effect = Exception("LLM failed")
        
        from backend.app.agents.mongodb_agent.agent import node_rewrite_query
        
        result = await node_rewrite_query({
            "query": "病房的设计要点？"
        })
        
        assert "search_terms" in result
        assert "rewrite_reason" in result
        print(f"✅ 查询改写: {result}")


async def test_node_search_mongodb():
    """测试搜索节点（Mock Retriever）"""
    # Mock retriever
    with patch('backend.app.agents.mongodb_agent.agent.get_retriever') as mock_retriever_fn:
        mock_retriever = MagicMock()
        mock_retriever_fn.return_value = mock_retriever
        
        # Mock smart search
        mock_retriever.smart_keyword_search.return_value = (
            [
                {
                    "chunk_id": "chunk_1",
                    "source_document": "规范文档",
                    "chunk_text": "病房的设计要点包括...",
                    "metadata": {"page": 1},
                }
            ],
            "text_index",
            {"attempts": ["search_terms"]},
        )
        
        from backend.app.agents.mongodb_agent.agent import node_search_mongodb
        from backend.app.agents.base_agent import AgentRequest
        
        result = await node_search_mongodb({
            "search_terms": ["病房"],
            "query": "病房的设计要点？",
            "request": AgentRequest(query="病房的设计要点？", top_k=5),
        })
        
        assert "retrieval_results" in result
        assert len(result["retrieval_results"]) == 1
        print(f"✅ 搜索节点: 找到 {len(result['retrieval_results'])} 条")


def test_node_format_results():
    """测试格式化结果节点"""
    from backend.app.agents.mongodb_agent.agent import node_format_results
    
    result = node_format_results({
        "retrieval_results": [
            {
                "chunk_id": "chunk_1",
                "source_document": "规范文档",
                "chunk_text": "病房的设计要点包括...",
                "metadata": {"page": 1},
            }
        ]
    })
    
    assert "items" in result
    assert len(result["items"]) == 1
    assert result["items"][0].source == "mongodb_agent"
    print(f"✅ 格式化结果: {len(result['items'])} 项")


# ============================================================================
# 测试：图结构
# ============================================================================

def test_graph_structure():
    """测试图结构"""
    from backend.app.agents.mongodb_agent.agent import graph
    
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
    llm1 = manager.get_or_create("test_mongodb", mock_init)
    assert init_count[0] == 1
    
    # 第二次获取（复用）
    llm2 = manager.get_or_create("test_mongodb", mock_init)
    assert init_count[0] == 1
    assert llm1 is llm2
    
    print("✅ LLM Manager 使用正确")


# ============================================================================
# 运行所有测试
# ============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("🧪 开始测试 MongoDB Agent")
    print("=" * 60 + "\n")
    
    # 同步测试
    print("📋 测试去重...")
    test_deduplicate_terms()
    
    print("\n📋 测试启发式改写...")
    test_heuristic_rewrite()
    
    print("\n📋 测试提取查询节点...")
    test_node_extract_query()
    
    print("\n📋 测试格式化结果节点...")
    test_node_format_results()
    
    print("\n📋 测试图结构...")
    test_graph_structure()
    
    print("\n📋 测试 LLM Manager...")
    test_llm_manager_usage()
    
    # 异步测试
    print("\n📋 测试查询改写节点...")
    asyncio.run(test_node_rewrite_query())
    
    print("\n📋 测试搜索节点...")
    asyncio.run(test_node_search_mongodb())
    
    print("\n" + "=" * 60)
    print("✅ 所有测试通过！")
    print("=" * 60)
