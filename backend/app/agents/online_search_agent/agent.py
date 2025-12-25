"""Online Search Agent - 优化版本

核心改进：
- ✅ 规范类型注解（使用 AgentRequest）
- ✅ 精简代码结构
- ✅ 规范接口（返回 items）
- ✅ 添加 asyncio 支持
"""

from __future__ import annotations

import asyncio
import os
import logging
from typing import Any, Dict, List
from typing_extensions import TypedDict

from langgraph.graph import END, StateGraph
from langchain_community.tools.tavily_search import TavilySearchResults

from backend.app.agents.base_agent import AgentItem, AgentRequest

logger = logging.getLogger("online_search_agent")


# ============================================================================
# 状态定义
# ============================================================================

class OnlineSearchState(TypedDict, total=False):
    """Online Search Agent 状态"""
    # 输入
    request: AgentRequest
    query: str
    search_mode: str  # "supplement" | "deep_search"
    
    # 处理
    search_results: List[Dict[str, Any]]
    
    # 输出
    items: List[AgentItem]
    diagnostics: Dict[str, Any]


# ============================================================================
# 节点函数
# ============================================================================

async def node_extract_query(state: OnlineSearchState) -> Dict[str, Any]:
    """提取查询"""
    request = state.get("request")
    if request and hasattr(request, "query"):
        query = request.query
    else:
        query = state.get("query", "")
    
    search_mode = state.get("search_mode", "supplement")
    
    logger.info(f"[OnlineSearch→ExtractQuery] 查询: {query}, 模式: {search_mode}")
    
    return {
        "query": query,
        "search_mode": search_mode,
    }


async def node_search(state: OnlineSearchState) -> Dict[str, Any]:
    """执行在线搜索（使用 Tavily）"""
    query = state.get("query", "")
    search_mode = state.get("search_mode", "supplement")
    
    logger.info(f"[OnlineSearch→Search] 执行搜索: {query} (mode={search_mode})")
    
    diagnostics = {"search_mode": search_mode}
    
    # 检查 API Key
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        logger.warning("[OnlineSearch] TAVILY_API_KEY 未设置，返回占位结果")
        search_results = [{
            "title": f"在线资源（占位）: {query}",
            "snippet": f"缺少 TAVILY_API_KEY，使用占位补充信息: '{query}'",
            "url": "https://example.com/placeholder",
            "source": "placeholder",
        }]
        diagnostics.update({
            "engine": "placeholder",
            "result_count": len(search_results),
            "reason": "missing_api_key",
        })
        return {
            "search_results": search_results,
            "diagnostics": diagnostics,
        }
    
    # 使用 Tavily 搜索
    try:
        tavily_tool = TavilySearchResults(max_results=5)
        
        # 使用 asyncio.to_thread 避免阻塞（因为 Tavily 是同步的）
        raw_results = await asyncio.to_thread(tavily_tool.invoke, query)
        
        search_results = []
        for res in raw_results:
            search_results.append({
                "title": res.get("title") or f"搜索结果: {query[:30]}",
                "snippet": res.get("content", ""),
                "url": res.get("url", ""),
                "source": "tavily",
            })
        
        diagnostics.update({
            "engine": "tavily",
            "result_count": len(search_results),
        })
        logger.info(f"[OnlineSearch→Search] Tavily 返回 {len(search_results)} 条结果")
    
    except Exception as e:
        logger.error(f"[OnlineSearch] Tavily 搜索失败: {e}")
        search_results = [{
            "title": "Tavily 搜索失败",
            "snippet": f"执行在线搜索时出错: {e}",
            "url": "",
            "source": "error",
        }]
        diagnostics.update({
            "engine": "tavily",
            "result_count": len(search_results),
            "error": str(e),
        })
    
    return {
        "search_results": search_results,
        "diagnostics": diagnostics,
    }


async def node_format_results(state: OnlineSearchState) -> Dict[str, Any]:
    """格式化搜索结果为 AgentItem"""
    search_results = state.get("search_results", [])
    
    logger.info(f"[OnlineSearch→Format] 格式化 {len(search_results)} 条结果")
    
    items = []
    for idx, result in enumerate(search_results):
        item = AgentItem(
            entity_id=f"online_search_{idx}",
            name=result.get("title", "未命名"),
            label="OnlineSearchResult",
            snippet=result.get("snippet", "")[:300],
            score=0.6,  # 在线搜索默认分数
            source="online_search_agent",
            attrs={
                "url": result.get("url", ""),
                "source_type": result.get("source", "online"),
            },
            citations=[{
                "source": "online_search",
                "url": result.get("url", ""),
            }],
        )
        items.append(item)
    
    diagnostics = state.get("diagnostics", {})
    diagnostics["items_count"] = len(items)
    
    return {
        "items": items,
        "diagnostics": diagnostics,
    }


# ============================================================================
# 构建图
# ============================================================================

def build_online_search_graph():
    """构建 Online Search Agent 图"""
    builder = StateGraph(OnlineSearchState)
    
    # 添加节点
    builder.add_node("extract_query", node_extract_query)
    builder.add_node("search", node_search)
    builder.add_node("format", node_format_results)
    
    # 设置流程
    builder.set_entry_point("extract_query")
    builder.add_edge("extract_query", "search")
    builder.add_edge("search", "format")
    builder.add_edge("format", END)
    
    logger.info("[OnlineSearch] 图构建完成")
    
    return builder.compile()


# ============================================================================
# 导出图
# ============================================================================

graph = build_online_search_graph()

logger.info("[OnlineSearch] 图已导出（纯 StateGraph 模式）")