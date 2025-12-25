from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import quote
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

# ============================================================================
# 导入 base_agent 的标准组件
# ============================================================================
from backend.app.agents.base_agent import (
    AgentItem,
    AgentRequest,
    WorkerResponsesAnnotated,  # ✅ 使用标准类型
    get_llm_manager,  # ✅ 使用 LLM 管理器
)

try:
    from openai import RateLimitError as OpenAIRateLimitError
except Exception:
    OpenAIRateLimitError = Exception

logger = logging.getLogger("result_synthesizer_agent")


# ============================================================================
# LLM 初始化（使用 LLMManager）
# ============================================================================

def _init_synthesizer_llm():
    """
        初始化 Synthesizer LLM
    """
    api_key = os.getenv("RESULT_SYNTHESIZER_AGENT_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("RESULT_SYNTHESIZER_AGENT_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    base_url = base_url.rstrip("/") if base_url else None
    model_provider = os.getenv("RESULT_SYNTHESIZER_AGENT_PROVIDER") or os.getenv("OPENAI_MODEL_PROVIDER") or "openai"
    model = os.getenv("RESULT_SYNTHESIZER_AGENT_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))

    if not api_key:
        raise ValueError("缺少 API KEY，请设置 RESULT_SYNTHESIZER_AGENT_API_KEY 或 OPENAI_API_KEY")

    # 强制使用 OpenAI 兼容模式（支持第三方 API Gateway）
    return init_chat_model(
        model=model,
        model_provider=model_provider,
        api_key=api_key,
        base_url=base_url,
        temperature=0.3,
        max_tokens=8000,
        timeout=120,       # [FIX 2025-12-04] 增加超时时间到120秒（原30秒太短）
    )


def _init_evaluator_llm():
    """初始化评估 LLM（可以使用不同的模型）"""
    api_key = os.getenv("EVALUATOR_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("EVALUATOR_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    base_url = base_url.rstrip("/") if base_url else None
    model_provider = os.getenv("EVALUATOR_MODEL_PROVIDER") or os.getenv("OPENAI_MODEL_PROVIDER") or "openai"
    model = os.getenv("EVALUATOR_MODEL", "gpt-4o-mini")

    if not api_key:
        raise ValueError("缺少评估器 API KEY，请设置 EVALUATOR_API_KEY 或 OPENAI_API_KEY")

    # 强制使用 OpenAI 兼容模式（支持第三方 API Gateway）
    return init_chat_model(
        model=model,
        model_provider=model_provider,
        api_key=api_key,
        base_url=base_url,
        temperature=0.0,  # 评估需要确定性
        max_tokens=200,
        timeout=30,       # 添加30秒超时，避免长时间等待
    )


# ============================================================================
# 辅助函数
# ============================================================================

# [FIX 2025-12-09] 增加超时时间，从 45 秒增加到 180 秒（3 分钟）
# 原因：复杂查询需要更多时间进行综合分析，避免频繁超时导致输出质量下降
SYNTHESIZER_TIMEOUT = int(os.getenv("RESULT_SYNTHESIZER_TIMEOUT", "180"))


async def _call_llm_with_retry(
    llm_name: str,
    messages: List[Any],
    purpose: str,
    max_attempts: int = 5,  # [FIX 2025-12-09] 从 3 次增加到 5 次
    initial_delay: float = 2.0  # [FIX 2025-12-09] 从 1.5 秒增加到 2 秒
):
    """
    带重试的异步 LLM 调用（使用 LLMManager）

    参数:
        llm_name: LLM 名称（用于从 LLMManager 获取）
        messages: 消息列表
        purpose: 用途描述（用于日志）
        max_attempts: 最大重试次数（默认 5 次）
        initial_delay: 初始延迟（秒，默认 2 秒）

    [FIX 2025-12-09] 增强重试策略:
    - 增加最大重试次数到 5 次（原 3 次）
    - 增加初始延迟到 2 秒（原 1.5 秒）
    - 配合更长的超时时间（180秒），确保复杂查询能够完成
    """
    import asyncio

    # ✅ 使用 LLMManager 获取 LLM
    manager = get_llm_manager()

    # 根据 llm_name 选择初始化函数，使用异步版本避免阻塞调用
    if llm_name == "synthesizer":
        llm = await asyncio.to_thread(lambda: manager.get_or_create(name=llm_name, init_func=_init_synthesizer_llm))
    elif llm_name == "evaluator":
        llm = await asyncio.to_thread(lambda: manager.get_or_create(name=llm_name, init_func=_init_evaluator_llm))
    else:
        raise ValueError(f"Unknown LLM name: {llm_name}")

    delay = initial_delay
    last_error: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await asyncio.wait_for(llm.ainvoke(messages), timeout=SYNTHESIZER_TIMEOUT)
        except OpenAIRateLimitError as err:
            last_error = err
            logger.warning(
                "[Synthesizer→LLM] %s 调用触发限流（attempt=%s/%s），将在 %.1fs 后重试: %s",
                purpose,
                attempt,
                max_attempts,
                delay,
                err,
            )
        except asyncio.TimeoutError as err:
            last_error = err
            logger.warning(
                "[Synthesizer→LLM] %s 超时（attempt=%s/%s），将在 %.1fs 后重试: %s",
                purpose,
                attempt,
                max_attempts,
                delay,
                err,
            )
        except Exception as err:
            last_error = err
            message = str(err).lower()

            if "429" in message or "rate" in message or "quota" in message:
                logger.warning(
                    "[Synthesizer→LLM] %s 遇到限流/配额问题（attempt=%s/%s），将在 %.1fs 后重试: %s",
                    purpose,
                    attempt,
                    max_attempts,
                    delay,
                    err,
                )
            elif "connection" in message or "network" in message:
                logger.warning(
                    "[Synthesizer→LLM] %s 网络连接失败（attempt=%s/%s），将在 %.1fs 后重试: %s",
                    purpose,
                    attempt,
                    max_attempts,
                    delay,
                    err,
                )
            else:
                logger.error(
                    "[Synthesizer→LLM] %s 调用失败（非瞬时错误），不再重试: %s",
                    purpose,
                    err,
                )
                raise

        if attempt < max_attempts:
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 8.0)

    # 所有重试都失败
    assert last_error is not None
    logger.error(
        "[Synthesizer→LLM] %s 在 %s 次尝试后仍失败，抛出最后错误: %s",
        purpose,
        max_attempts,
        last_error,
    )
    raise last_error


def _normalize_doc_name(candidate: Optional[str]) -> str:
    """清洗资料名称，去掉多余空格和全角空格。"""
    if not candidate:
        return ""
    return str(candidate).replace("\u3000", " ").strip()


def _page_sort_key(value: str) -> tuple[int, str]:
    """用于页码排序的key，优先按数字排序。"""
    digits = "".join(ch for ch in value if ch.isdigit())
    order = int(digits) if digits else 10**6
    return (order, value)


def _classify_document_role(doc_name: str) -> tuple[str, int]:
    """
    粗略判断资料的角色及排序优先级。

    🔧 [FIX] 降低"资料集"优先级，平衡各资料源权重
    返回: (role, priority)
    新优先级：指南/手册(0) → 详图/图集(1) → 规范/标准(2) → 资料集(3) → 其他(4)
    """
    name = doc_name or ""
    lower = name.lower()

    # 🔧 [FIX] 指南和手册提升为最高优先级
    if ("指南" in name) or ("手册" in name) or ("guide" in lower):
        return "guide", 0
    # 🔧 [FIX] 详图集提升为第二优先级
    if ("详图" in name) or ("图集" in name) or ("详解" in name):
        return "detail_atlas", 1
    # 🔧 [FIX] 规范标准保持重要地位
    if ("规范" in name) or ("标准" in name) or ("gb" in lower):
        return "code_spec", 2
    # 🔧 [FIX] 资料集降低到第四优先级，避免长期占主导
    if ("资料" in name) or ("资料集" in name):
        return "macro_atlas", 3
    return "other", 4


def _build_document_views(items: List[AgentItem]) -> List[Dict[str, Any]]:
    """
    将多Agent的结果按资料聚合，方便LLM生成“资料链路”叙述。

    返回字段示例：
    [
        {
            "doc_name": "《医院建筑设计指南》",
            "aliases": ["医院建筑设计指南", "GB51039-2014"],
            "agents": ["mongodb_agent", "neo4j_agent"],
            "pages": ["59", "60"],
            "locations": ["59页|第3章 寻路系统"],
            "highlights": [...],
            "images": [{"image_url": "...", "caption": "..."}],
            "citations": [...],
            "item_count": 3,
        },
        ...
    ]
    """
    documents: Dict[str, Dict[str, Any]] = {}
    unnamed_counter = 0

    for item in items:
        # 在线搜索单独处理，不并入本地图谱资料
        if (item.source or "").lower() == "online_search_agent":
            continue

        attrs = item.attrs or {}
        source_documents_value = attrs.get("source_documents")
        if isinstance(source_documents_value, (list, tuple)):
            first_source_document = source_documents_value[0] if source_documents_value else None
        else:
            first_source_document = source_documents_value

        doc_candidates = [
            attrs.get("source_document"),
            first_source_document,
            attrs.get("doc_title"),
            item.name,
            item.source,
        ]

        doc_name: Optional[str] = None
        for candidate in doc_candidates:
            normalized = _normalize_doc_name(candidate)
            if normalized and normalized.lower() not in {"unknown", "multiple"}:
                doc_name = normalized
                break

        if not doc_name:
            unnamed_counter += 1
            doc_name = f"未标注资料#{unnamed_counter}"

        doc_key = _normalize_doc_name(doc_name).lower() or f"__doc_{unnamed_counter}"
        entry = documents.setdefault(
            doc_key,
            {
                "doc_name": doc_name,
                "aliases": set(),
                "agents": set(),
                "pages": set(),
                "locations": set(),
                "file_paths": set(),
                "highlights": [],
                "images": [],
                "citations": [],
                "item_count": 0,
            },
        )

        entry["aliases"].update(
            {_normalize_doc_name(candidate) for candidate in doc_candidates if _normalize_doc_name(candidate)}
        )
        entry["agents"].add(item.source or "unknown")
        entry["item_count"] += 1

        highlight = {
            "title": item.name or item.label or doc_name,
            "label": item.label or "",
            "snippet": (item.snippet or "")[:500],
            "score": item.score,
            "source": item.source,
            "location": attrs.get("location"),
            "attrs": attrs,
        }
        entry["highlights"].append(highlight)

        citations = item.citations or []
        for citation in citations:
            page = citation.get("page_number")
            if page is not None:
                entry["pages"].add(str(page))

            location = citation.get("location")
            if location:
                entry["locations"].add(location)

            metadata = citation.get("metadata") or {}
            file_path = citation.get("file_path") or metadata.get("file_path")
            if file_path:
                entry["file_paths"].add(file_path)

            image_url = citation.get("image_url")
            if image_url:
                entry["images"].append(
                    {
                        "image_url": image_url,
                        "caption": citation.get("snippet") or highlight["snippet"],
                        "location": location,
                        "page_number": page,
                        "source": citation.get("source") or doc_name,
                    }
                )

            if len(entry["citations"]) < 40:
                entry["citations"].append(citation)

        attr_image = attrs.get("image_url")
        if attr_image:
            entry["images"].append(
                {
                    "image_url": attr_image,
                    "caption": highlight["snippet"],
                    "location": attrs.get("location"),
                    "page_number": None,
                    "source": doc_name,
                }
            )

    document_views: List[Dict[str, Any]] = []
    for entry in documents.values():
        images = []
        seen_images = set()
        for image in entry["images"]:
            url = image.get("image_url")
            if not url or url in seen_images:
                continue
            seen_images.add(url)
            images.append(image)
            if len(images) >= 6:
                break

        role, role_priority = _classify_document_role(entry["doc_name"])

        # 基于页码生成范围提示（如 P181-P186）
        page_span = ""
        if entry["pages"]:
            try:
                page_numbers = sorted({int("".join(filter(str.isdigit, p)) or "0") for p in entry["pages"]})
                if page_numbers:
                    first, last = page_numbers[0], page_numbers[-1]
                    page_span = f"P{first}" if first == last else f"P{first}-P{last}"
            except Exception:
                page_span = ""

        doc_view = {
            "doc_name": entry["doc_name"],
            "aliases": sorted(alias for alias in entry["aliases"] if alias),
            "agents": sorted(entry["agents"]),
            "pages": sorted(entry["pages"], key=_page_sort_key),
            "locations": sorted(entry["locations"]),
            "file_paths": list(entry["file_paths"]),
            "highlights": entry["highlights"][:6],
            "images": images,
            "citations": entry["citations"][:20],
            "item_count": entry["item_count"],
            "role": role,
            "role_priority": role_priority,
            "page_span": page_span,
            "page_count": len(entry["pages"]),
        }
        document_views.append(doc_view)

    # 🔧 [FIX] 添加跨资料平衡：Round-Robin混排，避免单一资料占主导
    # 先按优先级分组
    priority_groups = {}
    for doc_view in document_views:
        priority = doc_view.get("role_priority", 4)
        if priority not in priority_groups:
            priority_groups[priority] = []
        priority_groups[priority].append(doc_view)

    # 在每个优先级组内，按item_count降序排列，但确保多样性
    balanced_views = []
    for priority in sorted(priority_groups.keys()):
        group_docs = priority_groups[priority]

        if len(group_docs) <= 3:
            # 如果文档数量少，直接按item_count排序
            group_docs.sort(key=lambda doc: -doc.get("item_count", 0))
            balanced_views.extend(group_docs)
        else:
            # 如果文档数量多，使用Round-Robin确保多样性
            # 先按item_count分为高中低三档
            sorted_by_count = sorted(group_docs, key=lambda doc: -doc.get("item_count", 0))
            high_tier = sorted_by_count[:len(sorted_by_count)//3 or 1]
            mid_tier = sorted_by_count[len(sorted_by_count)//3:len(sorted_by_count)*2//3 or 1]
            low_tier = sorted_by_count[len(sorted_by_count)*2//3:]

            # Round-Robin选择
            max_len = max(len(high_tier), len(mid_tier), len(low_tier))
            for i in range(max_len):
                if i < len(high_tier):
                    balanced_views.append(high_tier[i])
                if i < len(mid_tier):
                    balanced_views.append(mid_tier[i])
                if i < len(low_tier):
                    balanced_views.append(low_tier[i])

    # 第二次平衡：在不同文档角色之间做Round-Robin，避免同一类型连续出现
    role_buckets: Dict[str, deque] = defaultdict(deque)
    for doc in balanced_views:
        role_buckets[doc.get("role", "other")].append(doc)

    # 按照角色优先级排序，优先输出高价值角色
    sorted_roles = sorted(
        role_buckets.keys(),
        key=lambda role: min(doc.get("role_priority", 4) for doc in role_buckets[role])
    )

    final_views: List[Dict[str, Any]] = []
    max_len = max(len(bucket) for bucket in role_buckets.values()) if role_buckets else 0

    for idx in range(max_len):
        for role in sorted_roles:
            bucket = role_buckets[role]
            if bucket:
                final_views.append(bucket.popleft())

    return final_views


def _build_rule_based_answer(
    query: str,
    aggregated_items: List[AgentItem],
    notes: List[str],
    documents_view: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """基于规则的兜底答案生成（优化版 2025-01-17）"""
    lines = [f"## 查询：{query}\n"]

    if aggregated_items:
        lines.append("### [综] 综合结果\n")
        for idx, item in enumerate(aggregated_items[:10], 1):
            title = item.name or item.label or item.entity_id or "未命名"
            source = item.source or "unknown"
            score = f"{item.score:.2f}" if item.score else "N/A"

            lines.append(f"{idx}. **{title}** (来源: {source}, 分数: {score})")

            if item.snippet:
                lines.append(f"   > {item.snippet[:200]}")

            if item.citations:
                cite_texts = []
                for cite in item.citations[:3]:
                    if isinstance(cite, dict):
                        source_name = cite.get("source", "引用")
                        cite_texts.append(source_name)
                if cite_texts:
                    lines.append(f"   引用: {', '.join(cite_texts)}")

            lines.append("")
    else:
        lines.append("[提示] 未找到相关结果，建议调整查询或加载更多数据。")

    if documents_view:
        lines.append("\n### [档] 资料链路")
        for doc in documents_view[:4]:
            page_hint = ""
            if doc.get("pages"):
                page_hint = f"（页码: {', '.join(doc['pages'][:3])}）"

            lines.append(f"#### {doc.get('doc_name', '未标注资料')}{page_hint}")

            highlight = (doc.get("highlights") or [{}])[0]
            snippet = highlight.get("snippet") or ""
            if snippet:
                lines.append(snippet)

            if doc.get("locations"):
                lines.append(f"- 位置: {', '.join(doc['locations'][:3])}")
            if doc.get("agents"):
                lines.append(f"- 数据来源: {', '.join(doc['agents'])}")

            image = (doc.get("images") or [{}])[0]
            image_url = image.get("image_url")
            if image_url:
                caption = image.get("caption") or doc.get("doc_name", "配图")
                lines.append(f"![{caption}]({image_url})")

            lines.append("")

    if notes:
        lines.append("\n### [诊] 诊断信息")
        for note in notes:
            lines.append(f"- {note}")

    # 默认推荐问题（优化版）
    recommended_questions: List[str] = []
    if aggregated_items:
        topics: List[str] = []
        for item in aggregated_items[:3]:
            topic = item.name or item.label
            if topic and topic not in topics:
                topics.append(topic)
        for topic in topics[:2]:
            recommended_questions.append(f"关于「{topic}」的详细规范和标准是什么？")
            recommended_questions.append(f"「{topic}」在实际项目中的应用案例？")

    recommended_questions.append(f"[深度搜索] 是否需要对「{query}」进行在线深度搜索？")
    recommended_questions.append(f"「{query}」的常见设计挑战和解决方案？")

    # 去重并限制到5个
    recommended_questions = list(dict.fromkeys(recommended_questions))[:5]

    if recommended_questions:
        lines.append("\n---\n")
        lines.append("### [问] 进一步探索\n")
        for idx, question in enumerate(recommended_questions, 1):
            lines.append(f"{idx}. {question}")

    final_answer = "\n".join(lines)

    # [FIX 2025-12-04] 提取图片引用
    image_references = []
    if documents_view:
        for doc in documents_view[:6]:
            for image in doc.get("images", [])[:2]:
                image_references.append({
                    **image,
                    "doc_name": doc.get("doc_name"),
                })
                if len(image_references) >= 10:
                    break
            if len(image_references) >= 10:
                break

    return {
        "final_answer": final_answer,
        "recommended_questions": recommended_questions,
        "notes": notes,
        # ✅ [FIX 2025-12-04] 添加图片引用
        "image_references": image_references,
    }


# ============================================================================
# 状态定义（使用标准类型）
# ============================================================================

class SynthesizerState(TypedDict, total=False):
    """
    Synthesizer 状态 - 优化版本

    关键改进：
    - ✅ 使用 WorkerResponsesAnnotated（标准类型）
    - ✅ 从 State 直接读取 worker_responses（而非 request.metadata）
    - ✅ [2025-11-25] 支持 answer_graph_data 输出
    """
    # 输入
    request: AgentRequest
    query: str
    worker_responses: WorkerResponsesAnnotated  # ✅ 标准类型

    # ✅ [2025-11-25] Knowledge Fusion 输出
    unified_hints: Dict[str, Any]  # 统一检索线索
    answer_graph_data: Dict[str, Any]  # 答案图谱数据（供前端可视化）

    # 处理
    aggregated_items: List[AgentItem]
    notes: List[str]

    # 反馈循环
    retry_count: int
    feedback_message: str  # 来自评估的反馈
    quality_score: float
    is_quality_good: bool

    # 输出
    final_answer: str
    recommended_questions: List[str]


# ============================================================================
# 节点函数
# ============================================================================

async def node_aggregate(state: SynthesizerState) -> Dict[str, Any]:
    """
    聚合多个 Worker 的响应
    
    关键改进：
    - ✅ 从 State 读取 worker_responses（而非 request.metadata）
    - ✅ worker_responses 是 List[Dict]（标准格式）
    """
    worker_responses = state.get("worker_responses", [])
    
    if not worker_responses:
        logger.warning("[Synthesizer→Aggregate] 无 Worker 响应")
        return {
            "aggregated_items": [],
            "notes": ["无任何智能体响应"],
        }
    
    # ✅ worker_responses 格式：
    # [
    #   {
    #     "agent_name": "neo4j_agent",
    #     "items": [...],
    #     "diagnostics": {...},
    #     "used_query": "...",
    #     "took_ms": 123,
    #     "item_count": 5,
    #   },
    #   ...
    # ]
    
    # 收集所有 items
    all_items = []
    for worker_resp in worker_responses:
        agent_name = worker_resp.get("agent_name", "unknown")
        items = worker_resp.get("items", [])
        
        for item in items:
            # 标记来源
            if not item.source:
                item.source = agent_name
            all_items.append(item)
    
    # 去重（基于 entity_id）
    seen = set()
    unique_items = []
    for item in all_items:
        key = item.entity_id or id(item)
        if key not in seen:
            seen.add(key)
            unique_items.append(item)
    
    # 按分数排序
    unique_items.sort(key=lambda x: x.score or 0.0, reverse=True)
    
    logger.info(f"[Synthesizer→Aggregate] 聚合得到 {len(unique_items)} 条结果")
    
    # 统计 Worker 贡献
    worker_stats = {}
    for worker_resp in worker_responses:
        agent_name = worker_resp.get("agent_name", "unknown")
        item_count = worker_resp.get("item_count", 0)
        worker_stats[agent_name] = item_count
    
    logger.info(f"[Synthesizer→Aggregate] Worker 统计: {worker_stats}")
    
    return {
        "aggregated_items": unique_items,
        "notes": [f"聚合了 {len(worker_responses)} 个智能体的响应"],
    }


def _normalize_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, (list, tuple, set)):
        out: List[str] = []
        for item in value:
            s = str(item).strip()
            if s:
                out.append(s)
        return out
    cleaned = str(value).strip()
    return [cleaned] if cleaned else []


def _is_strict_cross_doc_request(query: str, request: Optional[AgentRequest]) -> bool:
    """
    启发式触发：
    - 用户明确要求“仅/只基于…不要引用其它资料”
    - 且 filters.doc_ids/source_documents 指定了资料范围（尤其是多份）
    """
    q = (query or "").strip()
    if not q:
        return False

    filters = (request.filters or {}) if isinstance(request, AgentRequest) else {}
    doc_ids = _normalize_str_list(filters.get("doc_ids") or filters.get("doc_id"))
    source_documents = _normalize_str_list(filters.get("source_documents") or filters.get("source_document"))

    has_scope = bool(doc_ids or source_documents)
    if not has_scope:
        return False

    wants_strict = any(
        key in q
        for key in (
            "仅基于",
            "只基于",
            "不要引用其它资料",
            "不要引用其他资料",
            "不要引用其",
            "交叉验证",
            "一致要求",
            "差异/补充",
            "仍需核验",
            "每条都必须带引用",
        )
    )

    # 更偏严格：至少两份资料 或 明确“仅基于/不要引用其它资料”
    return bool((len(doc_ids) + len(source_documents) >= 2) or ("不要引用" in q) or ("仅基于" in q) or wants_strict)


def _citation_to_dict(cite: Any) -> Dict[str, Any]:
    if cite is None:
        return {}
    if isinstance(cite, dict):
        return {k: v for k, v in cite.items()}
    if hasattr(cite, "model_dump"):
        try:
            return cite.model_dump()
        except Exception:
            pass
    return {
        "source": getattr(cite, "source", ""),
        "location": getattr(cite, "location", ""),
        "snippet": getattr(cite, "snippet", ""),
        "chunk_id": getattr(cite, "chunk_id", None),
        "page_number": getattr(cite, "page_number", None),
        "section": getattr(cite, "section", None),
        "metadata": getattr(cite, "metadata", None),
        "positions": getattr(cite, "positions", None),
        "image_url": getattr(cite, "image_url", None),
        "content_type": getattr(cite, "content_type", None),
        "doc_id": getattr(cite, "doc_id", None),
    }


def _build_balanced_citations_for_strict_cross_doc(
    aggregated_items: List[AgentItem],
    request: Optional[AgentRequest],
    max_citations: int = 30,
) -> List[Dict[str, Any]]:
    """
    为“严格交叉验证”准备 citations：
    - 优先文本 citations（避免图片占位挤占额度）
    - 在多 doc_id 场景做 round-robin，确保两份资料都有证据可引用
    """
    from backend.app.utils.citation_builder import normalize_citations

    filters = (request.filters or {}) if isinstance(request, AgentRequest) else {}
    ordered_doc_ids = _normalize_str_list(filters.get("doc_ids") or filters.get("doc_id"))

    # 收集所有 citations（不限制每 item 3 条，避免证据不足）
    all_citations: List[Dict[str, Any]] = []
    for item in aggregated_items:
        for cite in (item.citations or []):
            data = _citation_to_dict(cite)
            if data:
                all_citations.append(data)

    def is_image(c: Dict[str, Any]) -> bool:
        return bool(c.get("image_url")) or (c.get("content_type") == "image")

    # 严格交叉验证：默认过滤图片 citations（除非用户明确要图）
    query = (request.query if isinstance(request, AgentRequest) else "") or ""
    wants_images = any(k in query for k in ("带图", "配图", "平面图", "剖面图", "流程图", "示意图", "图示"))
    filtered = [c for c in all_citations if wants_images or (not is_image(c))]

    # 去重：同一 doc_id + chunk/image + page/loc 只保留 1 条
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for c in filtered:
        key = (
            c.get("doc_id") or c.get("source"),
            c.get("chunk_id") or c.get("image_url") or "",
            c.get("page_number"),
            c.get("location") or "",
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)

    # 分组并 round-robin，避免某一本独占
    buckets: Dict[str, deque] = defaultdict(deque)
    for c in deduped:
        bucket_key = str(c.get("doc_id") or c.get("source") or "unknown")
        buckets[bucket_key].append(c)

    ordered_keys: List[str] = []
    if ordered_doc_ids:
        ordered_keys.extend([k for k in ordered_doc_ids if k in buckets])
    # 追加其余 bucket（按数量降序）
    ordered_keys.extend(
        [k for k, _ in sorted(buckets.items(), key=lambda kv: -len(kv[1])) if k not in ordered_keys]
    )

    selected: List[Dict[str, Any]] = []
    max_len = max((len(buckets[k]) for k in ordered_keys), default=0)
    for i in range(max_len):
        for k in ordered_keys:
            if buckets[k]:
                selected.append(buckets[k].popleft())
                if len(selected) >= max_citations:
                    break
        if len(selected) >= max_citations:
            break

    return normalize_citations(selected)


def _format_citations_catalog(citations: List[Dict[str, Any]], max_snippet_chars: int = 220) -> str:
    lines: List[str] = []
    for idx, c in enumerate(citations, start=1):
        source = str(c.get("source") or "").strip() or "未知来源"
        location = str(c.get("location") or "").strip()
        snippet = str(c.get("snippet") or "").strip()
        snippet = re.sub(r"\s+", " ", snippet)
        if len(snippet) > max_snippet_chars:
            snippet = snippet[: max_snippet_chars - 1] + "…"
        loc_part = f" | {location}" if location else ""
        snip_part = f" | {snippet}" if snippet else ""
        lines.append(f"[{idx}] {source}{loc_part}{snip_part}")
    return "\n".join(lines)


def _validate_strict_cross_doc_answer(answer: str, citations_count: int) -> List[str]:
    """返回 violations 列表；空列表表示通过。"""
    violations: List[str] = []
    text = (answer or "").strip()
    if not text:
        return ["empty_answer"]

    required_titles = [
        "【一致要求】",
        "【差异/补充（标注仅见于哪份资料）】",
        "【仍需核验】",
    ]
    positions = {t: text.find(t) for t in required_titles}
    missing = [t for t, p in positions.items() if p < 0]
    if missing:
        violations.append(f"missing_sections:{','.join(missing)}")
        return violations

    ordered = sorted(((t, positions[t]) for t in required_titles), key=lambda x: x[1])
    for idx, (title, start) in enumerate(ordered):
        end = ordered[idx + 1][1] if idx + 1 < len(ordered) else len(text)
        block = text[start + len(title) : end]
        bullet_lines = [ln for ln in block.splitlines() if ln.strip().startswith("-")]
        if not bullet_lines:
            violations.append(f"no_bullets_in:{title}")
            continue
        for ln in bullet_lines:
            nums = [int(n) for n in re.findall(r"\[(\d+)\]", ln)]
            if not nums:
                violations.append(f"bullet_missing_citation:{title}:{ln.strip()[:60]}")
                continue
            bad = [n for n in nums if n < 1 or n > citations_count]
            if bad:
                violations.append(f"citation_out_of_range:{title}:{bad}:{ln.strip()[:60]}")

    # 额外拦截明显外部标准编号，避免“只基于两份资料”时泄漏。
    if re.search(r"\bGB\s*\d{3,6}\b", text, re.IGNORECASE) or re.search(r"\bWS/?T\s*\d", text, re.IGNORECASE):
        violations.append("external_standard_id_mentioned")

    return violations


async def node_synthesize(state: SynthesizerState) -> Dict[str, Any]:
    """
    合成最终答案（优先使用 LLM，失败时回退到规则）

    [UPGRADED] 2025-01-15: 生成更丰富的输出
    - 显示知识图谱查询路径和扩展的知识点
    - 显示文档引用的详细位置（页码、章节）
    - 集成在线搜索补充信息
    - 生成智能推荐问题

    关键改进：
    - ✅ 使用 LLMManager 获取 LLM
    - ✅ 在 prompt 中使用 feedback_message（反馈循环）
    - ✅ 生成结构化输出（章节分明）
    """
    query = state.get("query", "")
    aggregated_items = state.get("aggregated_items", [])
    worker_responses = state.get("worker_responses", [])
    notes = state.get("notes", [])
    retry_count = state.get("retry_count", 0)
    feedback_message = state.get("feedback_message", "")
    document_views = _build_document_views(aggregated_items)

    logger.info(f"[Synthesizer→Synthesize] 合成答案，共 {len(aggregated_items)} 条结果 (retry={retry_count})")

    # 如果有反馈，记录
    if feedback_message:
        logger.info(f"[Synthesizer→Synthesize] 使用反馈改进答案: {feedback_message}")

    # 无数据时返回兜底答案
    if not aggregated_items:
        logger.info("[Synthesizer→Synthesize] 无数据可用，返回默认提示")
        fallback = _build_rule_based_answer(query, aggregated_items, notes, document_views)
        return fallback

    # ============================================================================
    # [NEW] 提取各Agent的特殊信息
    # ============================================================================
    neo4j_query_path = None
    mongodb_citations = []
    milvus_citations = []
    online_search_results = []
    image_citations = []  # ✅ [NEW] 提取图片信息
    neo4j_citations = []  # ✅ [NEW] Neo4j引用

    for resp in worker_responses:
        agent_name = resp.get("agent_name", "")

        # Neo4j: 提取知识图谱查询路径
        if agent_name == "neo4j_agent":
            diagnostics = resp.get("diagnostics", {})
            neo4j_query_path = diagnostics.get("query_path")
            for item in resp.get("items", []):
                for citation in item.citations or []:
                    neo4j_citations.append({
                        "source": citation.get("source"),
                        "snippet": citation.get("snippet", "")[:200],
                        "entity": item.name,
                    })

        # MongoDB: 提取文档引用和图片
        elif agent_name == "mongodb_agent":
            for item in resp.get("items", []):
                for citation in item.citations or []:
                    location = citation.get("location", "")
                    if location and location != "位置未知":
                        # 提取文本chunk引用
                        if citation.get("content_type") != "image":
                            mongodb_citations.append({
                                "source": citation.get("source", ""),
                                "location": location,
                                "snippet": citation.get("snippet", "")[:100],
                            })
                        # ✅ [NEW] 提取图片chunk
                        else:
                            image_url = citation.get("image_url")
                            if image_url:
                                image_citations.append({
                                    "image_url": image_url,
                                    "location": location,
                                    "source": citation.get("source", ""),
                                    "snippet": citation.get("snippet", "")[:100],
                                })

        # Milvus: 提取属性引用
        elif agent_name == "milvus_agent":
            for item in resp.get("items", []):
                for citation in item.citations or []:
                    if citation.get("attribute_type"):
                        milvus_citations.append({
                            "entity": item.name,
                            "source": citation.get("source", ""),
                            "location": citation.get("location", ""),
                            "attribute_type": citation.get("attribute_type", ""),
                            "snippet": citation.get("snippet", "")[:100]
                        })

        # Online Search: 提取在线补充（取5-10条）
        elif agent_name == "online_search_agent":
            for item in resp.get("items", []):
                online_search_results.append({
                    "title": item.name,
                    "url": item.attrs.get("url", "") if item.attrs else "",
                    "snippet": item.snippet[:200] if item.snippet else "",  # 增加snippet长度
                    "score": item.score if item.score else 0.0
                })

            # 按分数排序，取前10条
            online_search_results = sorted(
                online_search_results,
                key=lambda x: x["score"],
                reverse=True
            )[:10]

    documents_payload = document_views[:6]
    top_documents = documents_payload[:4]  # 保证后续使用时已定义，避免未赋值错误

    document_images: List[Dict[str, Any]] = []
    for doc in top_documents:
        for image in doc.get("images", []):
            annotated = dict(image)
            annotated["doc_name"] = doc.get("doc_name")
            document_images.append(annotated)
            if len(document_images) >= 10:
                break
        if len(document_images) >= 10:
            break

    if document_images:
        image_citations = document_images

    # 资料角色视图（用于跨资料逻辑串联）
    doc_roles = []
    for doc in top_documents:
        doc_roles.append(
            {
                "doc_name": doc.get("doc_name"),
                "role": doc.get("role"),
                "priority": doc.get("role_priority"),
                "agents": doc.get("agents"),
                "pages": doc.get("pages"),
                "locations": doc.get("locations"),
                "images": len(doc.get("images", []) or []),
                "items": doc.get("item_count"),
            }
        )

    # ============================================================================
    # [NEW] 构建增强的上下文（包含特殊信息）
    # ============================================================================
    doc_distribution = {doc.get("doc_name"): doc.get("item_count", 0) for doc in top_documents if doc.get("doc_name")}

    # ✅ [2025-11-25] 从 state 获取 Knowledge Fusion 输出
    answer_graph_data = state.get("answer_graph_data", {})
    unified_hints = state.get("unified_hints", {})

    # ✅ [2025-11-25] 从 request.metadata 获取 answer_graph_data（兼容旧版）
    request = state.get("request")
    if not answer_graph_data and request and request.metadata:
        answer_graph_data = request.metadata.get("answer_graph_data", {})
        unified_hints = request.metadata.get("unified_hints", {})

    enhanced_context = {
        "query": query,
        "total_results": len(aggregated_items),
        "knowledge_graph": neo4j_query_path,  # 知识图谱路径
        "document_citations": mongodb_citations[:10],  # MongoDB引用（增加到10个）
        "attribute_citations": milvus_citations[:10],  # Milvus引用（增加到10个）
        "online_supplements": online_search_results,  # 在线补充（5-10条，已排序）
        "related_images": image_citations[:10],  # ✅ [NEW] 相关图片（增加到10个）
        "knowledge_graph_citations": neo4j_citations[:10],  # ✅ [NEW] Neo4j来源引用
        "documents_view": top_documents,
        "doc_roles": doc_roles,
        "doc_distribution": doc_distribution,
        "documents_total": len(document_views),
        "items_summary": [],
        "key_takeaways": [],
        # ✅ [2025-11-25] 添加 unified_hints 供 LLM 参考
        "unified_hints": unified_hints if unified_hints else None,
    }

    # 提取items的简要信息
    top_items = aggregated_items[:6]
    for item in top_items:
        source_doc = item.attrs.get("source_document") or (
            (item.attrs.get("source_documents") or [None])[0]
        )
        enhanced_context["items_summary"].append({
            "title": item.name or item.label or item.entity_id or "未命名",
            "source": item.source or "unknown",
            "source_document": source_doc or "unknown",
            "score": float(item.score) if item.score else 0.0,
            "snippet": (item.snippet or "")[:220],
            "citations": item.citations or [],
        })

    # 生成关键洞察，方便 LLM 快速理解要点
    key_takeaways = []
    for item in top_items[:3]:
        snippet = (item.snippet or "").strip().replace("\n", " ")
        if not snippet:
            continue
        key_takeaways.append(f"{item.name or item.label or '资料'}：{snippet[:160]}")
    if not key_takeaways and query:
        key_takeaways.append(f"聚焦主题：{query}")
    enhanced_context["key_takeaways"] = key_takeaways

    # ============================================================================
    # [NEW] ??????PDF???2025-01-17 ???
    # ============================================================================
    project_root = Path(__file__).resolve().parents[3]
    documents_dir = (project_root / "backend" / "databases" / "documents").resolve()

    def _attach_pdf_metadata(citation: Dict[str, Any]) -> None:
        file_path = citation.get("file_path")
        document_path = citation.get("document_path")
        page_number = citation.get("page_number")

        abs_path: Optional[Path] = None
        rel_path: Optional[str] = document_path

        if file_path:
            file_path_obj = Path(file_path)
            abs_candidate = file_path_obj if file_path_obj.is_absolute() else (documents_dir / file_path_obj)
            try:
                abs_path = abs_candidate.resolve()
            except Exception:
                abs_path = None
        elif document_path:
            abs_candidate = documents_dir / document_path
            try:
                abs_path = abs_candidate.resolve()
            except Exception:
                abs_path = None

        if abs_path:
            citation["file_path"] = abs_path.as_posix()
            try:
                rel_path = abs_path.relative_to(documents_dir).as_posix()
            except ValueError:
                rel_path = rel_path or None

        if rel_path:
            citation["document_path"] = rel_path
            encoded = quote(rel_path)
            base_endpoint = f"/documents/pdf?path={encoded}"
            citation["pdf_url"] = base_endpoint
            citation["pdf_link"] = f"{base_endpoint}#page={page_number}" if page_number else base_endpoint
            citation.pop("link_placeholder", None)
        else:
            citation["pdf_link"] = "#"
            citation["pdf_url"] = None
            citation["link_placeholder"] = True

    for citation in mongodb_citations:
        _attach_pdf_metadata(citation)

    for citation in milvus_citations:
        _attach_pdf_metadata(citation)
        citation["link_placeholder"] = True

    # ============================================================================
    # [OPTIMIZED 2025-12-03] 优化的 System Prompt（增强可读性）
    # ============================================================================
    system_prompt = """你是 MediArch 综合医院建筑顾问。结合 key_takeaways、items_summary、documents_view 提供的事实，输出结构清晰、可执行且让人愿意阅读的答案。

写作要求：
1. 采用中文，语气温和但专业，避免堆砌 bullet，优先使用完整段落。
2. 每段控制 2~4 句话，说明“发现 → 原因/证据 → 对设计的影响”。
3. 对关键信息添加引用，按 `[n]` 对应 citations。
4. 在结尾提供【设计建议】与【下一步行动】，让读者立即落实。

推荐结构：
### 开场概览
- 用一段话概述用户问题与整体答案走向。

### 关键洞察
- 结合 key_takeaways 与 items_summary，写 2~3 段，从“功能/规范/场景”三个角度说明启发。

### 资料印证
- 将最重要的 3~4 份 documents_view 资料穿成故事，展示它们如何互补（无需把所有资料都铺开）。

### 设计建议
- 至少 2 条，包含明确动作、参数或协作对象。

### 风险与注意
- 提醒潜在红线、适用条件或需要进一步验证的内容。

### 引用
- 按 `- [n] 资料名称（页码/章节）` 格式列出引用，确保与正文标注一致。
"""

    # ✅ [2025-12-03] 简化 user_prompt，移除冗余指令
    user_prompt = f"""用户问题：{query}

请基于以下检索结果生成回答：
{json.dumps(enhanced_context, ensure_ascii=False, indent=2)}"""

    try:
        # ✅ 使用 LLMManager
        response = await _call_llm_with_retry(
            llm_name="synthesizer",
            messages=[
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ],
            purpose="synthesize",
        )

        final_answer = response.content.strip()

        # ============================================================================
        # [NEW] 生成智能推荐问题（优化版 2025-01-17）
        # ============================================================================
        recommended_questions = []

        # 策略1: 基于知识图谱扩展的实体生成深入问题（优先级最高）
        if neo4j_query_path and neo4j_query_path.get("expanded_entities"):
            for entity in neo4j_query_path["expanded_entities"][:3]:  # 取前3个
                entity_name = entity.get("name", "")
                entity_type = entity.get("type", "")
                if entity_name and entity_name != query:
                    if entity_type:
                        recommended_questions.append(f"{entity_name}在{entity_type}中的详细设计要求和规范标准？")
                    else:
                        recommended_questions.append(f"{entity_name}的详细设计要求和相关案例？")

        # 策略2: 基于关系扩展生成关联问题
        if neo4j_query_path and neo4j_query_path.get("expanded_relations"):
            for rel in neo4j_query_path["expanded_relations"][:2]:  # 取前2个
                source = rel.get("source", "")
                target = rel.get("target", "")
                relation = rel.get("relation", "")
                if source and target:
                    if relation:
                        recommended_questions.append(f"{source}与{target}之间的{relation}关系如何在实际设计中体现？")
                    else:
                        recommended_questions.append(f"{source}与{target}的空间关系和流线设计要点？")

        # 策略3: 基于知识覆盖领域生成横向拓展问题
        if neo4j_query_path and neo4j_query_path.get("knowledge_coverage"):
            domains = [cov.get("domain", "") for cov in neo4j_query_path["knowledge_coverage"][:2]]
            for domain in domains:
                if domain:
                    recommended_questions.append(f"在{domain}方面还有哪些相关的设计规范和最佳实践？")

        # 策略4: 基于在线搜索结果生成补充问题
        if online_search_results and len(online_search_results) >= 2:
            # 如果有在线搜索结果，说明本地知识可能不足，引导用户探索新方向
            recommended_questions.append(f"关于「{query}」的最新行业趋势和前沿技术应用有哪些？")

        # 策略5: 基于检索结果质量生成深度搜索建议
        if len(aggregated_items) < 5:
            recommended_questions.append(f"[深度搜索] 是否需要对「{query}」进行在线深度搜索以获取更多资料？")

        # 策略6: 相关案例询问（总是添加）
        recommended_questions.append(f"能否提供{query}的实际案例和项目经验分享？")

        # 策略7: 实践应用问题（总是添加）
        recommended_questions.append(f"{query}在实际项目中的常见挑战和解决方案？")

        # 去重并限制数量（保持3-5个）
        seen = set()
        unique_questions = []
        for q in recommended_questions:
            if q not in seen:
                seen.add(q)
                unique_questions.append(q)

        recommended_questions = unique_questions[:5]  # 最多5个

        logger.info("[Synthesizer->Synthesize] LLM 合成成功，生成推荐问题: %d 个", len(recommended_questions))

        return {
            "final_answer": final_answer,
            "recommended_questions": recommended_questions,
            "notes": notes,
            # ✅ [2025-11-25] 输出 answer_graph_data 供前端可视化
            "answer_graph_data": answer_graph_data,
            "unified_hints": unified_hints,
            # ✅ [FIX 2025-12-04] 添加图片引用返回，供前端显示
            "image_references": image_citations[:10] if image_citations else [],
            # ✅ [FIX 2025-12-04] 添加文档引用详情，供前端PDF跳转
            "document_citations": {
                "mongodb": mongodb_citations[:10],
                "milvus": milvus_citations[:10],
                "neo4j": neo4j_citations[:10],
            },
        }

    except Exception as err:
        logger.error(f"[Synthesizer→Synthesize] LLM 合成失败: {err}，使用规则兜底")
        fallback = _build_rule_based_answer(query, aggregated_items, notes, document_views)
        fallback["notes"] = notes
        return fallback


async def node_evaluate_quality(state: SynthesizerState) -> Dict[str, Any]:
    """
    评估答案质量
    
    关键改进：
    - ✅ 使用 LLMManager 获取评估 LLM
    """
    query = state.get("query", "")
    final_answer = state.get("final_answer", "")
    aggregated_items = state.get("aggregated_items", [])
    retry_count = state.get("retry_count", 0)
    
    logger.info(f"[Synthesizer→Evaluate] 评估答案质量 (retry={retry_count})")
    
    citation_count = sum(len(item.citations or []) for item in aggregated_items)
    sources = list({item.source for item in aggregated_items if item.source})
    
    system_prompt = """你是医院建筑设计领域的质量评估专家。

评估以下答案的质量：

查询：{query}
答案：{answer}
引用数量：{citation_count}
数据来源：{sources}
结果条数：{item_count}

评估标准：
1. 信息完整性 - 是否完整回答了问题（权重 40%）
2. 引用充分性 - 是否有足够的数据支持（权重 30%）
3. 专业准确性 - 信息是否专业可靠（权重 30%）

返回格式（必须是有效的 JSON）：
{
  "quality_score": 0.85,
  "is_quality_good": true,
  "feedback": "建议补充急诊部的具体面积要求"
}
"""
    
    user_payload = {
        "query": query,
        "answer": final_answer[:1500],
        "citation_count": citation_count,
        "sources": sources,
        "item_count": len(aggregated_items),
    }
    
    try:
        # ✅ 使用 LLMManager
        response = await _call_llm_with_retry(
            llm_name="evaluator",
            messages=[
                SystemMessage(content=system_prompt),
                HumanMessage(content=json.dumps(user_payload, ensure_ascii=False)),
            ],
            purpose="evaluate",
        )
        
        content = response.content.strip()
        
        # 清理 JSON（移除 markdown 代码块）
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        
        eval_data = json.loads(content)
        quality_score = float(eval_data.get("quality_score", 0.8))
        is_quality_good = bool(eval_data.get("is_quality_good", quality_score >= 0.7))
        feedback = eval_data.get("feedback", "") or ""
        
        logger.info(
            "[Synthesizer→Evaluate] 质量分数: %.2f, 合格: %s",
            quality_score,
            is_quality_good,
        )
        
        return {
            "quality_score": quality_score,
            "is_quality_good": is_quality_good,
            "feedback_message": feedback if not is_quality_good else "",
        }
    
    except Exception as err:
        logger.warning(f"[Synthesizer→Evaluate] LLM 评估失败: {err}，使用启发式兜底")
        is_quality_good = len(aggregated_items) >= 3 and citation_count >= 2
        return {
            "quality_score": 0.7 if is_quality_good else 0.5,
            "is_quality_good": is_quality_good,
            "feedback_message": "需要更多相关信息" if not is_quality_good else "",
        }


def route_after_evaluation(state: SynthesizerState) -> str:
    """评估后的路由决策"""
    is_quality_good = state.get("is_quality_good", False)
    retry_count = state.get("retry_count", 0)
    
    if is_quality_good:
        logger.info("[Synthesizer→Route] 质量合格，输出最终答案")
        return "finalize"
    else:
        if retry_count < 2:
            logger.info(f"[Synthesizer→Route] 质量不合格，准备重试 (retry={retry_count})")
            return "request_retry"
        else:
            logger.warning("[Synthesizer→Route] 已达最大重试次数，标注后输出")
            return "finalize_with_warning"


def node_finalize(state: SynthesizerState) -> Dict[str, Any]:
    """最终化：输出答案"""
    final_answer = state.get("final_answer", "")
    
    logger.info("[Synthesizer→Finalize] 输出最终答案")
    
    return {"final_answer": final_answer}


def node_finalize_with_warning(state: SynthesizerState) -> Dict[str, Any]:
    """最终化（带警告）：信息可能不完整"""
    final_answer = state.get("final_answer", "")
    
    warning = "\n\n⚠️ **提示**：当前答案可能信息不完整，建议调整查询或联系专业人员获取更详细的信息。"
    final_answer_with_warning = final_answer + warning
    
    logger.info("[Synthesizer→FinalizeWithWarning] 添加警告并输出")
    
    return {"final_answer": final_answer_with_warning}


def node_request_retry(state: SynthesizerState) -> Dict[str, Any]:
    """请求重试：增加重试计数并传递反馈"""
    retry_count = state.get("retry_count", 0)
    feedback_message = state.get("feedback_message", "")
    
    new_retry_count = retry_count + 1
    
    logger.info(f"[Synthesizer→RequestRetry] 请求重试，retry_count: {retry_count} → {new_retry_count}")
    logger.info(f"[Synthesizer→RequestRetry] 反馈信息: {feedback_message}")
    
    return {
        "retry_count": new_retry_count,
        "feedback_message": feedback_message,
    }


# ============================================================================
# 构建图
# ============================================================================

def build_synthesizer_graph():
    """
    构建 Synthesizer 图 - 简化版本 (2025-12-03)

    关键改进：
    - ✅ 移除评估和重试循环（效果不佳且耗时）
    - ✅ 简化流程：aggregate → synthesize → END
    - ✅ 使用标准类型和 LLMManager
    """
    builder = StateGraph(SynthesizerState)

    # 添加节点（简化版：只保留核心节点）
    builder.add_node("aggregate", node_aggregate)
    builder.add_node("synthesize", node_synthesize)

    # 设置流程（直接流程，无评估循环）
    builder.set_entry_point("aggregate")
    builder.add_edge("aggregate", "synthesize")
    builder.add_edge("synthesize", END)

    logger.info("[Synthesizer] 图构建完成（简化版本，无评估循环）")

    return builder.compile()


# ============================================================================
# 导出图（供 LangGraph Studio 使用）
# ============================================================================

# ✅ 使用纯函数构建（删除 BaseAgent 类）
graph = build_synthesizer_graph()

logger.info("[Synthesizer] 图已导出")
