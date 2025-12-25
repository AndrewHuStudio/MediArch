#!/usr/bin/env python3
"""测试 Online Search Agent"""

import os
import sys
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ============================================================================
# 测试：节点函数
# ============================================================================

def test_node_extract_query():
    """测试提取查询节点"""
    from backend.app.agents.online_search_agent.agent import node_extract_query
    from backend.app.agents.base_agent import AgentRequest
    
    result = node_extract_query({
        "request": AgentRequest(query="医院建筑设计规范"),
        "search_mode": "supplement",
    })
    
    assert result["query"] == "医院建筑设计规范"
    assert result["search_mode"] == "supplement"
    print(f"✅ 提取查询: {result}")


async def test_node_search_no_api_key():
    """测试搜索节点（无 API Key）"""
    # 临时清空环境变量
    with patch.dict(os.environ, {"TAVILY_API_KEY": ""}, clear=False):
        from backend.app.agents.online_search_agent.agent import node_search
        
        result = await node_search({
            "query": "医院建筑设计规范",
            "search_mode": "supplement",
        })
        
        assert "search_results" in result
        assert len(result["search_results"]) == 1
        assert result["diagnostics"]["reason"] == "missing_api_key"
        print(f"✅ 搜索节点（无 API Key）: {result['diagnostics']}")


async def test_node_search_with_mock_tavily():
    """测试搜索节点（Mock Tavily）"""
    # Mock TavilySearchResults
    with patch('backend.app.agents.online_search_agent.agent.TavilySearchResults') as mock_tavily_class:
        mock_tavily = MagicMock()
        mock_tavily_class.return_value = mock_tavily
        
        # Mock invoke 返回值
        mock_tavily.invoke.return_value = [
            {
                "title": "医院建筑设计规范",
                "content": "医院建筑设计规范内容...",
                "url": "https://example.com/spec",
            }
        ]
        
        from backend.app.agents.online_search_agent.agent import node_search
        
        result = await node_search({
            "query": "医院建筑设计规范",
            "search_mode": "supplement",
        })
        
        assert "search_results" in result
        assert len(result["search_results"]) == 1
        assert result["diagnostics"]["engine"] == "tavily"
        print(f"✅ 搜索节点（Mock Tavily）: 找到 {len(result['search_results'])} 条")


def test_node_format_results():
    """测试格式化结果节点"""
    from backend.app.agents.online_search_agent.agent import node_format_results
    
    result = node_format_results({
        "search_results": [
            {
                "title": "医院建筑设计规范",
                "snippet": "医院建筑设计规范内容...",
                "url": "https://example.com/spec",
                "source": "tavily",
            }
        ],
        "diagnostics": {},
    })
    
    assert "items" in result
    assert len(result["items"]) == 1
    assert result["items"][0].source == "online_search_agent"
    assert result["items"][0].name == "医院建筑设计规范"
    print(f"✅ 格式化结果: {len(result['items'])} 项")


# ============================================================================
# 测试：图结构
# ============================================================================

def test_graph_structure():
    """测试图结构"""
    from backend.app.agents.online_search_agent.agent import graph
    
    assert graph is not None
    assert hasattr(graph, 'ainvoke')
    print("✅ 图结构正确")


# ============================================================================
# 测试：完整流程
# ============================================================================

async def test_full_pipeline():
    """测试完整流程（Mock Tavily）"""
    # Mock TavilySearchResults
    with patch('backend.app.agents.online_search_agent.agent.TavilySearchResults') as mock_tavily_class:
        mock_tavily = MagicMock()
        mock_tavily_class.return_value = mock_tavily
        
        mock_tavily.invoke.return_value = [
            {
                "title": "医院建筑设计规范",
                "content": "医院建筑设计规范内容...",
                "url": "https://example.com/spec",
            }
        ]
        
        from backend.app.agents.online_search_agent.agent import graph
        from backend.app.agents.base_agent import AgentRequest
        
        result = await graph.ainvoke({
            "request": AgentRequest(query="医院建筑设计规范"),
            "search_mode": "supplement",
        })
        
        assert "items" in result
        assert len(result["items"]) == 1
        assert result["items"][0].source == "online_search_agent"
        print(f"✅ 完整流程: 返回 {len(result['items'])} 项")


# ============================================================================
# 运行所有测试
# ============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("🧪 开始测试 Online Search Agent")
    print("=" * 60 + "\n")
    
    # 同步测试
    print("📋 测试提取查询节点...")
    test_node_extract_query()
    
    print("\n📋 测试格式化结果节点...")
    test_node_format_results()
    
    print("\n📋 测试图结构...")
    test_graph_structure()
    
    # 异步测试
    print("\n📋 测试搜索节点（无 API Key）...")
    asyncio.run(test_node_search_no_api_key())
    
    print("\n📋 测试搜索节点（Mock Tavily）...")
    asyncio.run(test_node_search_with_mock_tavily())
    
    print("\n📋 测试完整流程...")
    asyncio.run(test_full_pipeline())
    
    print("\n" + "=" * 60)
    print("✅ 所有测试通过！")
    print("=" * 60)