from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict

from pydantic import BaseModel, Field
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from backend.app.agents.base_agent import AgentRequest, call_structured_llm, get_llm_manager
from backend.app.agents.online_search_policy import decide_online_search_usage
from backend.llm_env import get_api_key, get_llm_base_url, get_llm_model, get_model_provider

try:
    from openai import RateLimitError as OpenAIRateLimitError
except Exception:
    OpenAIRateLimitError = None

try:
    import httpx
    _HTTPX_ERRORS = (httpx.TimeoutException, httpx.ConnectError, httpx.ReadTimeout)
except Exception:
    _HTTPX_ERRORS = ()

logger = logging.getLogger("orchestrator_agent")

# 默认配置
DEFAULT_WORKERS = ["neo4j_agent", "milvus_agent", "mongodb_agent", "online_search_agent"]
DEFAULT_LOCAL_WORKERS = ["neo4j_agent", "milvus_agent", "mongodb_agent"]
DEFAULT_TOP_K = 20 
DEFAULT_TIMEOUT_MS = 3000

_HEALTHCARE_ARCHITECTURE_TERMS = (
    "医院",
    "医疗",
    "门诊",
    "急诊",
    "住院部",
    "护理单元",
    "病房",
    "手术室",
    "医技",
    "护士站",
    "icu",
    "ccu",
    "核医学",
    "放射",
    "导向",
    "候诊",
    "洁污",
    "流线",
    "建筑设计",
    "设计规范",
    "空间",
    "科室",
)


def _resolve_optional_timeout_seconds(env_name: str, default: int) -> Optional[int]:
    raw = os.getenv(env_name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    return None if value <= 0 else value


# ============================================================================
# 状态定义
# ============================================================================

class OrchestratorState(TypedDict, total=False):
    """Orchestrator 状态"""
    # 输入
    messages: List[BaseMessage]
    query: str
    available_workers: List[str]
    
    # 内部处理
    extracted_query: str
    
    # 输出
    is_hospital_related: bool
    general_answer: str
    agents_to_call: List[str]
    rewritten_query: str  # 查询改写
    request: AgentRequest
    diagnostics: Dict[str, Any]

class IntentAnalysisResult(BaseModel):
    """LLM 结构化输出：意图分析结果"""

    is_hospital_related: bool = Field(
        ...,
        description="问题是否与综合医院建筑设计相关",
    )
    rewritten_query: str = Field(
        default="",
        description="结合上下文改写后的完整问题",
    )
    general_answer: str = Field(
        default="",
        description="不相关时的引导回答",
    )
    reasoning: str = Field(
        default="",
        description="简要判断理由",
    )


# ============================================================================
# LLM 管理
# ============================================================================

def _init_orchestrator_llm():
    """初始化 Orchestrator LLM"""
    api_key = get_api_key()
    if not api_key:
        raise ValueError("缺少 MEDIARCH_API_KEY（orchestrator_agent）")
    
    base_url = get_llm_base_url()
    model = os.getenv("ORCHESTRATOR_MODEL") or get_llm_model("gpt-4o-mini")
    model_provider = get_model_provider()
    timeout_s = _resolve_optional_timeout_seconds("ORCHESTRATOR_TIMEOUT", 30)
    
    return init_chat_model(
        model=model,
        model_provider=model_provider,
        api_key=api_key,
        base_url=base_url,
        temperature=0.3,
        max_tokens=12000,
        timeout=timeout_s,
    )


async def get_orchestrator_llm():
    """获取 Orchestrator LLM（异步版本，修复阻塞调用问题）
    """
    import asyncio

    manager = get_llm_manager()

    # 检查是否已缓存
    if "orchestrator" in manager._instances:
        return manager._instances["orchestrator"]

    # 使用asyncio.to_thread()在独立线程中初始化LLM
    try:
        llm = await asyncio.to_thread(_init_orchestrator_llm)
        manager._instances["orchestrator"] = llm
        return llm
    except Exception as e:
        logger.warning(f"[OrchestratorAgent] LLM初始化失败: {e}")
        raise


# ============================================================================
# 辅助函数
# ============================================================================

def extract_query_from_messages(messages: List[BaseMessage]) -> str:
    """提取最后一条用户消息"""
    if not messages:
        return ""
    
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = msg.content
            if isinstance(content, str):
                return content.strip()
            elif isinstance(content, list):
                # 提取文本块
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") in {"text", "input_text"}:
                            text = block.get("text") or block.get("value") or ""
                            if text:
                                text_parts.append(text)
                return "\n".join(text_parts).strip()
        elif isinstance(msg, dict) and msg.get("type") == "human":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content.strip()
    
    return ""


def _looks_like_healthcare_architecture_query(query: str) -> bool:
    text = (query or "").strip().lower()
    if not text:
        return False
    return any(term in text for term in _HEALTHCARE_ARCHITECTURE_TERMS)


# ============================================================================
# 节点函数
# ============================================================================

async def node_extract_query(state: OrchestratorState) -> Dict[str, Any]:
    """提取用户查询"""
    messages = state.get("messages", [])
    query = state.get("query", "")
    
    # 如果 state 中已有 query，直接使用
    if query:
        extracted_query = query
    else:
        # 从 messages 中提取
        extracted_query = extract_query_from_messages(messages)
    
    logger.info(f"[Orchestrator→ExtractQuery] 提取查询: {extracted_query}")
    
    return {"extracted_query": extracted_query}


async def node_analyze_intent(state: OrchestratorState) -> Dict[str, Any]:
    """分析用户意图"""
    from backend.app.utils.llm_output_parser import parse_llm_output

    query = state.get("extracted_query", "")
    messages = state.get("messages", [])
    
    logger.info(f"[Orchestrator→AnalyzeIntent] 分析意图: {query}")

    # 对明显的医疗建筑问题优先走规则判定，避免 LLM 超时或误判导致 benchmark 无法进入检索链路。
    if _looks_like_healthcare_architecture_query(query):
        return {
            "is_hospital_related": True,
            "rewritten_query": query,
            "general_answer": "",
            "diagnostics": {
                "intent_reasoning": "rule_based_healthcare_architecture_match",
            },
        }
    
    # 构建上下文（最近3轮对话）
    recent_context = []
    for msg in messages[-6:]:  # 最近3轮（每轮user+assistant）
        if isinstance(msg, (HumanMessage, SystemMessage, AIMessage)):
            content = msg.content
            if isinstance(content, str):
                snippet = content
            elif isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") in {"text", "input_text"}:
                            text = block.get("text") or block.get("value") or ""
                            if text:
                                text_parts.append(text)
                snippet = "\n".join(text_parts).strip()
            else:
                snippet = ""
            if snippet:
                recent_context.append(f"{msg.__class__.__name__}: {snippet[:100]}")
    
    context_str = "\n".join(recent_context[-4:]) if recent_context else "无上下文"
    
    # System prompt
    system_prompt = """你是 MediArch 综合医院设计助手的意图分析专家。

你的任务：
1. 判断问题是否与医院建筑设计相关
2. 如果问题有代词引用（如"它"、"这个"、"那个"），结合上下文改写为完整问题
3. 返回 JSON 格式

判断标准：
- 相关：医院设计、科室规划、医疗建筑、设计规范等
- 不相关：天气、新闻、编程、娱乐等

返回格式（必须是有效 JSON）：
{
  "is_hospital_related": true,
  "rewritten_query": "改写后的完整问题（如果有代词）",
  "general_answer": "如果不相关，给出引导回答",
  "reasoning": "简要判断理由"
}
"""
    
    user_prompt = f"""上下文（最近对话）：
{context_str}

当前问题：{query}

请分析并返回 JSON。"""
    
    def _is_transient_error(error: Exception) -> bool:
        if isinstance(error, asyncio.TimeoutError):
            return True
        if OpenAIRateLimitError is not None and isinstance(error, OpenAIRateLimitError):
            return True
        if _HTTPX_ERRORS and isinstance(error, _HTTPX_ERRORS):
            return True
        message = str(error).lower()
        return any(
            keyword in message
            for keyword in (
                "timeout",
                "timed out",
                "temporarily",
                "connection",
                "network",
                "rate limit",
                "quota",
                "overloaded",
                "429",
            )
        )

    llm = await get_orchestrator_llm()
    max_attempts = 3
    result: IntentAnalysisResult | None = None
    structured_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = await call_structured_llm(
                llm=llm,
                pydantic_model=IntentAnalysisResult,
                messages=[
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt),
                ],
            )
            structured_error = None
            break
        except Exception as e:
            structured_error = e
            if attempt < max_attempts and _is_transient_error(e):
                delay = min(2.0 * attempt, 6.0)
                logger.warning(
                    "[Orchestrator→AnalyzeIntent] 瞬时错误，%s/%s 次重试后继续等待 %.1fs: %s",
                    attempt,
                    max_attempts,
                    delay,
                    e,
                )
                await asyncio.sleep(delay)
                continue
            logger.warning("[Orchestrator→AnalyzeIntent] 结构化输出失败，降级为手动解析: %s", e)
            break

    if result is None:
        try:
            raw_result = await llm.ainvoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt),
                ]
            )
            parsed = parse_llm_output(
                output=raw_result,
                pydantic_model=IntentAnalysisResult,
                fallback_parser=None,
            )
            if parsed is None:
                raise ValueError("manual parse returned None")
            result = parsed
            logger.info("[Orchestrator→AnalyzeIntent] 手动解析成功，继续执行")
        except Exception as e:
            logger.error("[Orchestrator→AnalyzeIntent] LLM 分析失败", exc_info=True)
            if structured_error is not None:
                logger.debug("[Orchestrator→AnalyzeIntent] Structured Output 最后一次错误: %s", structured_error)
            raise RuntimeError(
                "Orchestrator 结构化输出失败；"
                "请使用支持结构化输出的 OpenAI 兼容 API。"
            ) from e

    rewritten_query = result.rewritten_query.strip() if result.rewritten_query else ""
    if not rewritten_query:
        rewritten_query = query

    logger.info(
        f"[Orchestrator→AnalyzeIntent] 相关性: {result.is_hospital_related}, "
        f"改写: {rewritten_query if rewritten_query != query else '无'}"
    )

    return {
        "is_hospital_related": result.is_hospital_related,
        "rewritten_query": rewritten_query,
        "general_answer": result.general_answer or "",
        "diagnostics": {
            "intent_reasoning": result.reasoning,
        },
    }


async def node_decide_action(state: OrchestratorState) -> Dict[str, Any]:
    """决定下一步动作"""
    is_hospital_related = state.get("is_hospital_related", True)
    rewritten_query = state.get("rewritten_query", "")
    general_answer = state.get("general_answer", "")
    available_workers = state.get("available_workers")
    request = state.get("request")
    if available_workers is None:
        available_workers = DEFAULT_WORKERS
    
    logger.info(f"[Orchestrator→DecideAction] 相关性: {is_hospital_related}")
    
    diagnostics = state.get("diagnostics") or {}

    # 不相关问题
    if not is_hospital_related:
        final_answer = general_answer or (
            "这个问题不在我的专业领域内。\n\n"
            "我专注于综合医院建筑设计，如果您有相关问题，欢迎咨询！"
        )
        
        return {
            "general_answer": final_answer,
            "agents_to_call": [],
            "diagnostics": {**diagnostics, "type": "general_question"},
        }
    
    metadata = request.metadata if isinstance(request, AgentRequest) else {}
    online_search_decision = decide_online_search_usage(
        state.get("extracted_query", "") or rewritten_query,
        include_online_search=bool((metadata or {}).get("include_online_search")),
        deep_search=bool((metadata or {}).get("deep_search")),
        thinking_mode=bool((metadata or {}).get("thinking_mode")),
    )

    # 相关问题：选择 Workers
    workers = [w for w in DEFAULT_LOCAL_WORKERS if w in available_workers]
    if online_search_decision["enabled"] and "online_search_agent" in available_workers:
        workers.append("online_search_agent")
    if not workers:
        workers = list(available_workers or [])
        if not workers:
            logger.warning("[Orchestrator→DecideAction] 未找到可用 Worker")
    
    logger.info(f"[Orchestrator→DecideAction] 调用 Workers: {workers}")
    
    return {
        "agents_to_call": workers,
        "query": rewritten_query,  # 使用改写后的查询
        "diagnostics": {
            **diagnostics,
            "type": "hospital_related",
            "rewritten": rewritten_query != state.get("extracted_query", ""),
            "online_search_enabled": online_search_decision["enabled"],
            "online_search_reason": online_search_decision["reason"],
            "online_search_mode": online_search_decision["search_mode"],
        },
    }


async def node_prepare_request(state: OrchestratorState) -> Dict[str, Any]:
    """准备 AgentRequest"""
    query = state.get("query") or state.get("rewritten_query") or state.get("extracted_query", "")
    
    logger.info(f"[Orchestrator→PrepareRequest] 准备请求: {query}")

    # ✅ 关键：保留上游（API / MediArch Graph）传入的 filters / top_k / timeout 等参数。
    # 否则会导致 doc scoping（filters.doc_ids/source_documents）在 Orchestrator 阶段被覆盖丢失。
    existing = state.get("request")

    if isinstance(existing, AgentRequest):
        request = AgentRequest(
            query=query,
            filters=existing.filters or {},
            top_k=existing.top_k,
            lang=existing.lang,
            timeout_ms=existing.timeout_ms,
            trace_id=existing.trace_id,
            metadata=existing.metadata or {},
            context=list(existing.context or []),
            attachments=list(existing.attachments or []),
        )
    else:
        request = AgentRequest(
            query=query,
            filters={},
            top_k=DEFAULT_TOP_K,
            lang="zh",
            timeout_ms=DEFAULT_TIMEOUT_MS,
            trace_id=None,
            metadata={},
            context=[],
            attachments=[],
        )
    
    return {"request": request}


# ============================================================================
# 路由函数
# ============================================================================

def route_after_decide(state: OrchestratorState) -> str:
    """决定路由"""
    is_hospital_related = state.get("is_hospital_related", True)
    
    if is_hospital_related:
        return "prepare_request"
    else:
        return END


# ============================================================================
# 构建图
# ============================================================================

def build_orchestrator_graph():
    """构建 Orchestrator 图"""
    builder = StateGraph(OrchestratorState)
    
    # 添加节点
    builder.add_node("extract_query", node_extract_query)
    builder.add_node("analyze_intent", node_analyze_intent)
    builder.add_node("decide_action", node_decide_action)
    builder.add_node("prepare_request", node_prepare_request)
    
    # 设置流程
    builder.set_entry_point("extract_query")
    builder.add_edge("extract_query", "analyze_intent")
    builder.add_edge("analyze_intent", "decide_action")
    
    # 条件路由
    builder.add_conditional_edges(
        "decide_action",
        route_after_decide,
        {
            "prepare_request": "prepare_request",
            END: END,
        }
    )
    
    builder.add_edge("prepare_request", END)
    
    logger.info("[Orchestrator] 图构建完成")
    
    return builder.compile()


# ============================================================================
# 导出图
# ============================================================================

orchestrator_logic_graph = build_orchestrator_graph()
graph = orchestrator_logic_graph

logger.info("[Orchestrator] 图已导出")
