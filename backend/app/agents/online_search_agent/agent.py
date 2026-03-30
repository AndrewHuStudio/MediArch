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
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from typing_extensions import TypedDict

from langgraph.graph import END, StateGraph
from langchain_community.tools.tavily_search import TavilySearchResults

from backend.app.agents.base_agent import AgentItem, AgentRequest

logger = logging.getLogger("online_search_agent")


class OnlineSearchConfig:
    """Online Search 配置常量"""
    PREFERRED_DOMAINS = [
        "https://www.gooood.cn",
        "https://www.archdaily.com",
        "https://www.archdaily.cn",
        "https://www.greatbuildings.com",
    ]
    MAX_RESULTS_SUPPLEMENT = 5
    MAX_RESULTS_DEEP = 8
    MIN_DOMAIN_RESULTS = 2
    MAX_HINT_TERMS = 5
    MAX_SUBTOPIC_TERMS = 2
    MAX_QUERY_LENGTH = 220


_RE_MULTI_SPACE = re.compile(r"\s+")
_RE_ZH_TOKEN = re.compile(r"[\u4e00-\u9fa5]{2,6}")

_CASE_TERMS_ZH = ["医院", "医疗建筑", "建筑", "案例"]
_CASE_TERMS_EN = ["hospital", "healthcare", "architecture", "case study"]
_HOSPITAL_TERMS_ZH = ["医院", "医疗", "综合医院", "医技", "手术部", "病房"]
_HOSPITAL_TERMS_EN = ["hospital", "healthcare", "medical", "clinic", "medical center"]


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
# 辅助函数
# ============================================================================

def _normalize_query(text: str) -> str:
    cleaned = _RE_MULTI_SPACE.sub(" ", (text or "").strip())
    return cleaned[:OnlineSearchConfig.MAX_QUERY_LENGTH]


def _dedup_preserve(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items or []:
        key = (item or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _extract_hint_terms(request: AgentRequest | None) -> List[str]:
    if not request or not getattr(request, "metadata", None):
        return []
    meta = request.metadata or {}
    terms: List[str] = []

    unified = meta.get("unified_hints") or {}
    if isinstance(unified, dict):
        terms.extend([t for t in unified.get("search_terms", []) if isinstance(t, str)])
        terms.extend([t for t in unified.get("entity_names", []) if isinstance(t, str)])

    neo = meta.get("neo4j_expansion") or {}
    if isinstance(neo, dict):
        terms.extend([t for t in neo.get("search_terms", []) if isinstance(t, str)])

    subtopics = meta.get("subtopics") or []
    if isinstance(subtopics, list):
        terms.extend([t for t in subtopics if isinstance(t, str)])

    return _dedup_preserve(terms)[:OnlineSearchConfig.MAX_HINT_TERMS]


def _resolve_search_mode(state: OnlineSearchState, request: AgentRequest | None) -> str:
    search_mode = state.get("search_mode") or "supplement"
    if request and getattr(request, "metadata", None):
        meta = request.metadata or {}
        if meta.get("thinking_mode") or meta.get("deep_search") or meta.get("search_mode") == "deep_search":
            search_mode = "deep_search"
    if search_mode not in ("supplement", "deep_search"):
        search_mode = "supplement"
    return search_mode


def _resolve_lang(query: str, request: AgentRequest | None) -> str:
    lang = (request.lang if request else "") or "zh"
    if _RE_ZH_TOKEN.search(query):
        return "zh"
    return "zh" if lang.lower().startswith("zh") else "en"


def _compose_query(base_query: str, extra_terms: List[str], lang: str) -> str:
    parts = [base_query]
    parts.extend(extra_terms or [])

    case_terms = _CASE_TERMS_ZH if lang == "zh" else _CASE_TERMS_EN
    base_lower = base_query.lower()
    if lang == "zh":
        has_case = any(term in base_query for term in case_terms)
        if not has_case:
            parts.extend(case_terms)
    else:
        has_case = any(term in base_lower for term in case_terms)
        if not has_case:
            parts.extend(case_terms)

    combined = _normalize_query(" ".join(_dedup_preserve(parts)))
    return combined


def _domain_from_url(url: str) -> str:
    try:
        netloc = urlparse(url or "").netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


def _normalize_domain_entry(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        netloc = urlparse(raw).netloc.lower()
    except Exception:
        netloc = raw.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def _get_preferred_domains() -> List[str]:
    domains = [_normalize_domain_entry(v) for v in OnlineSearchConfig.PREFERRED_DOMAINS]
    return _dedup_preserve(domains)


def _compute_relevance_score(
    *,
    title: str,
    snippet: str,
    url: str,
    query: str,
    preferred_domains: List[str],
    raw_score: Optional[float] = None,
) -> float:
    domain = _domain_from_url(url)
    title_lower = (title or "").lower()
    snippet_lower = (snippet or "").lower()
    query_lower = (query or "").lower()

    score = 0.55
    if domain and any(domain.endswith(d) for d in preferred_domains):
        score += 0.25

    hospital_terms = _HOSPITAL_TERMS_ZH + _HOSPITAL_TERMS_EN
    if any(t in title or t in snippet for t in _HOSPITAL_TERMS_ZH) or any(
        t in title_lower or t in snippet_lower for t in _HOSPITAL_TERMS_EN
    ):
        score += 0.1

    case_terms = _CASE_TERMS_ZH + _CASE_TERMS_EN
    if any(t in title or t in snippet for t in _CASE_TERMS_ZH) or any(
        t in title_lower or t in snippet_lower for t in _CASE_TERMS_EN
    ):
        score += 0.05

    if query_lower and (query_lower in title_lower or query_lower in snippet_lower):
        score += 0.05

    if raw_score is not None:
        try:
            score = max(score, min(float(raw_score), 1.0))
        except Exception:
            pass

    return round(min(score, 0.95), 4)


def _dedup_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for res in results:
        url = (res.get("url") or "").strip()
        title = (res.get("title") or "").strip()
        snippet = (res.get("snippet") or "").strip()
        key = url or f"{title}|{snippet[:80]}"
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(res)
    return deduped


def _rank_results(
    results: List[Dict[str, Any]],
    *,
    query: str,
    preferred_domains: List[str],
    max_items: int,
) -> List[Dict[str, Any]]:
    ranked: List[Dict[str, Any]] = []
    for res in results:
        title = res.get("title", "")
        snippet = res.get("snippet", "")
        url = res.get("url", "")
        raw_score = res.get("raw_score")
        score = _compute_relevance_score(
            title=title,
            snippet=snippet,
            url=url,
            query=query,
            preferred_domains=preferred_domains,
            raw_score=raw_score,
        )
        res["score"] = score
        res["domain"] = _domain_from_url(url)
        ranked.append(res)

    ranked.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    return ranked[:max_items]


async def _run_tavily_search(
    *,
    query: str,
    max_results: int,
    search_depth: str,
    include_domains: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    tavily_tool = TavilySearchResults(
        max_results=max_results,
        search_depth=search_depth,
        include_domains=include_domains or [],
    )
    raw_results = await asyncio.to_thread(tavily_tool.invoke, query)
    normalized: List[Dict[str, Any]] = []
    for res in raw_results or []:
        normalized.append({
            "title": res.get("title") or f"搜索结果: {query[:30]}",
            "snippet": res.get("content", "") or res.get("snippet", ""),
            "url": res.get("url", ""),
            "source": "tavily",
            "raw_score": res.get("score"),
        })
    return normalized


def _build_search_plans(
    *,
    query: str,
    request: AgentRequest | None,
    search_mode: str,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    lang = _resolve_lang(query, request)
    hint_terms = _extract_hint_terms(request)
    base_query = _normalize_query(query)
    composed_query = _compose_query(base_query, hint_terms, lang)
    preferred_domains = _get_preferred_domains()

    max_results = (
        OnlineSearchConfig.MAX_RESULTS_DEEP
        if search_mode == "deep_search"
        else OnlineSearchConfig.MAX_RESULTS_SUPPLEMENT
    )
    search_depth = "advanced" if search_mode == "deep_search" else "basic"

    plans: List[Dict[str, Any]] = []

    # 主搜索计划：限定优选域名
    if preferred_domains:
        plans.append({
            "query": composed_query,
            "include_domains": preferred_domains,
            "max_results": max_results,
            "search_depth": search_depth,
            "reason": "preferred_domains",
        })

    # Fallback 搜索计划：不限域名，确保非建筑案例类查询也能获得结果
    fallback_max = max(max_results // 2, 3)
    plans.append({
        "query": composed_query,
        "include_domains": [],
        "max_results": fallback_max,
        "search_depth": search_depth,
        "reason": "fallback_open_web",
    })

    meta = request.metadata if request else {}
    subtopics = meta.get("subtopics", []) if isinstance(meta, dict) else []
    if search_mode == "deep_search" and isinstance(subtopics, list):
        for subtopic in subtopics[:OnlineSearchConfig.MAX_SUBTOPIC_TERMS]:
            subtopic_query = _compose_query(f"{base_query} {subtopic}", hint_terms, lang)
            plans.append({
                "query": subtopic_query,
                "include_domains": preferred_domains,
                "max_results": max_results,
                "search_depth": search_depth,
                "reason": f"preferred_domains:{subtopic}",
            })

    return plans, preferred_domains


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

    search_mode = _resolve_search_mode(state, request)

    logger.info(f"[OnlineSearch→ExtractQuery] 查询: {query}, 模式: {search_mode}")

    return {
        "query": query,
        "search_mode": search_mode,
    }


async def node_search(state: OnlineSearchState) -> Dict[str, Any]:
    """执行在线搜索（使用 Tavily）"""
    query = state.get("query", "")
    search_mode = state.get("search_mode", "supplement")
    request = state.get("request")
    
    logger.info(f"[OnlineSearch→Search] 执行搜索: {query} (mode={search_mode})")
    
    diagnostics = {"search_mode": search_mode}
    
    # 检查 API Key
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        logger.warning("[OnlineSearch] TAVILY_API_KEY 未设置，跳过在线搜索")
        search_results: List[Dict[str, Any]] = []
        diagnostics.update({
            "engine": "tavily",
            "result_count": 0,
            "reason": "missing_api_key",
        })
        return {
            "search_results": search_results,
            "diagnostics": diagnostics,
        }
    
    # 使用 Tavily 搜索
    try:
        plans, preferred_domains = _build_search_plans(
            query=query,
            request=request,
            search_mode=search_mode,
        )
        if not plans:
            diagnostics.update({
                "engine": "tavily",
                "result_count": 0,
                "reason": "no_search_plans",
            })
            return {
                "search_results": [],
                "diagnostics": diagnostics,
            }

        max_results = (
            OnlineSearchConfig.MAX_RESULTS_DEEP
            if search_mode == "deep_search"
            else OnlineSearchConfig.MAX_RESULTS_SUPPLEMENT
        )
        final_max = min(10, max_results + (4 if search_mode == "deep_search" else 0))

        search_results: List[Dict[str, Any]] = []
        plan_stats: List[Dict[str, Any]] = []

        for plan in plans:
            results = await _run_tavily_search(
                query=plan["query"],
                max_results=plan["max_results"],
                search_depth=plan["search_depth"],
                include_domains=plan["include_domains"],
            )
            plan_stats.append({
                "reason": plan["reason"],
                "query": plan["query"],
                "include_domains": plan["include_domains"],
                "result_count": len(results),
            })
            search_results.extend(results)

        search_results = _dedup_results(search_results)
        search_results = _rank_results(
            search_results,
            query=query,
            preferred_domains=preferred_domains,
            max_items=final_max,
        )

        diagnostics.update({
            "engine": "tavily",
            "result_count": len(search_results),
            "preferred_domains": preferred_domains,
            "plans": plan_stats,
        })
        logger.info(f"[OnlineSearch→Search] Tavily 返回 {len(search_results)} 条结果")
    
    except Exception as e:
        logger.error(f"[OnlineSearch] Tavily 搜索失败: {e}")
        search_results = []
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
        score = result.get("score")
        if score is None:
            score = 0.6
        item = AgentItem(
            entity_id=f"online_search_{idx}",
            name=result.get("title", "未命名"),
            label="OnlineSearchResult",
            snippet=result.get("snippet", "")[:300],
            score=score,  # 在线搜索分数（含域名优先级）
            source="online_search_agent",
            attrs={
                "url": result.get("url", ""),
                "source_type": result.get("source", "online"),
                "domain": result.get("domain", _domain_from_url(result.get("url", ""))),
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
