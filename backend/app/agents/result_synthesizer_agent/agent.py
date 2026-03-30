from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import quote
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Tuple
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
from backend.llm_env import get_api_key, get_llm_base_url, get_llm_model, get_model_provider

try:
    from openai import RateLimitError as OpenAIRateLimitError
except Exception:
    OpenAIRateLimitError = Exception

logger = logging.getLogger("result_synthesizer_agent")


def _resolve_optional_timeout_seconds(env_name: str, default: int) -> Optional[int]:
    raw = os.getenv(env_name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    return None if value <= 0 else value


# ============================================================================
# LLM 初始化（使用 LLMManager）
# ============================================================================

def _init_synthesizer_llm():
    """
        初始化 Synthesizer LLM
    """
    api_key = get_api_key()
    if not api_key:
        raise ValueError("缺少 MEDIARCH_API_KEY（result_synthesizer_agent）")

    base_url = get_llm_base_url() or "https://api.openai.com/v1"
    model_provider = get_model_provider()
    model = get_llm_model("gpt-4o-mini")

    # 强制使用 OpenAI 兼容模式（支持第三方 API Gateway）
    timeout_s = _resolve_optional_timeout_seconds("RESULT_SYNTHESIZER_TIMEOUT", 180)

    return init_chat_model(
        model=model,
        model_provider=model_provider,
        api_key=api_key,
        base_url=base_url,
        temperature=0.3,
        max_tokens=8000,
        timeout=timeout_s,
    )


def _init_evaluator_llm():
    """初始化评估 LLM（可以使用不同的模型）"""
    api_key = get_api_key()
    if not api_key:
        raise ValueError("缺少 MEDIARCH_API_KEY（result_synthesizer_evaluator）")

    base_url = get_llm_base_url() or "https://api.openai.com/v1"
    model_provider = get_model_provider()
    model = os.getenv("EVALUATOR_MODEL", get_llm_model("gpt-4o-mini"))

    timeout_s = _resolve_optional_timeout_seconds("RESULT_EVALUATOR_TIMEOUT", 30)

    return init_chat_model(
        model=model,
        model_provider=model_provider,
        api_key=api_key,
        base_url=base_url,
        temperature=0.0,  # 评估需要确定性
        max_tokens=200,
        timeout=timeout_s,
    )

SYNTHESIZER_TIMEOUT = _resolve_optional_timeout_seconds("RESULT_SYNTHESIZER_TIMEOUT", 180)
EVALUATOR_TIMEOUT = _resolve_optional_timeout_seconds("RESULT_EVALUATOR_TIMEOUT", 30)


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

    # ✅ 使用 LLMManager 获取 LLM（async-safe）
    manager = get_llm_manager()

    # 根据 llm_name 选择初始化函数（使用 async 版本避免竞态）
    if llm_name == "synthesizer":
        llm = await manager.aget_or_create(name=llm_name, init_func=_init_synthesizer_llm)
    elif llm_name == "evaluator":
        llm = await manager.aget_or_create(name=llm_name, init_func=_init_evaluator_llm)
    else:
        raise ValueError(f"Unknown LLM name: {llm_name}")

    delay = initial_delay
    last_error: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            llm_timeout = SYNTHESIZER_TIMEOUT if llm_name == "synthesizer" else EVALUATOR_TIMEOUT
            if llm_timeout is None:
                return await llm.ainvoke(messages)
            return await asyncio.wait_for(llm.ainvoke(messages), timeout=llm_timeout)
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
                        "file_path": citation.get("file_path") or metadata.get("file_path"),
                        "document_path": citation.get("document_path"),
                        "pdf_url": citation.get("pdf_url"),
                        "content_type": citation.get("content_type") or "image",
                        "chunk_id": citation.get("chunk_id"),
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
                    "file_path": attrs.get("file_path"),
                    "document_path": attrs.get("document_path"),
                    "pdf_url": attrs.get("pdf_url"),
                    "content_type": attrs.get("content_type") or "image",
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
    """基于规则的兜底答案生成（优化版 2026-01-12）

    关键改进：
    - 移除内部调试信息（agent来源、分数）
    - 统一引用格式为 [n]
    - 将推荐问题移到最后
    - 添加清晰的结构分隔
    """
    lines = []

    # 1. 核心答案部分
    if aggregated_items:
        lines.append(f"## {query}\n")
        lines.append("### 核心要点\n")

        # 提取前5个最重要的结果作为要点
        for idx, item in enumerate(aggregated_items[:5], 1):
            title = item.name or item.label or item.entity_id or "未命名"
            snippet = (item.snippet or "")[:150].strip()

            if snippet:
                lines.append(f"{idx}. **{title}**：{snippet}")
            else:
                lines.append(f"{idx}. **{title}**")

            lines.append("")
    else:
        lines.append(f"## {query}\n")
        lines.append("### 查询结果\n")
        lines.append("未找到相关结果。建议：")
        lines.append("- 调整查询关键词")
        lines.append("- 检查资料库是否已加载相关文档")
        lines.append("- 尝试使用在线深度搜索\n")

    # 2. 参考资料部分
    if documents_view:
        lines.append("\n## 参考资料\n")

        # 去重：同一资料只列出一次
        seen_docs = set()
        citation_idx = 1

        for doc in documents_view[:6]:
            doc_name = doc.get('doc_name', '未标注资料')

            # 跳过重复资料
            if doc_name in seen_docs:
                continue
            seen_docs.add(doc_name)

            # 构建位置信息
            location_parts = []
            if doc.get("pages"):
                pages = doc['pages'][:3]
                if len(pages) == 1:
                    location_parts.append(f"第{pages[0]}页")
                else:
                    location_parts.append(f"第{pages[0]}-{pages[-1]}页")

            if doc.get("locations"):
                location_parts.append(doc['locations'][0])

            location_str = " | ".join(location_parts) if location_parts else ""

            # 输出引用
            if location_str:
                lines.append(f"[{citation_idx}] {doc_name} | {location_str}")
            else:
                lines.append(f"[{citation_idx}] {doc_name}")

            # 添加摘要（如果有）
            highlight = (doc.get("highlights") or [{}])[0]
            snippet = (highlight.get("snippet") or "").strip()
            if snippet:
                # 清理snippet，移除多余空格和换行
                snippet = " ".join(snippet.split())[:200]
                lines.append(f"   {snippet}")

            lines.append("")
            citation_idx += 1

    # 3. 诊断信息（仅在有重要提示时显示）
    if notes:
        important_notes = [n for n in notes if not any(
            skip in n for skip in ["聚合了", "个智能体", "agent", "分数"]
        )]
        if important_notes:
            lines.append("\n## 提示信息\n")
            for note in important_notes:
                lines.append(f"- {note}")
            lines.append("")

    # 4. 推荐问题（移到最后）
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

    # 去重并限制到5个
    recommended_questions = list(dict.fromkeys(recommended_questions))[:5]

    if recommended_questions:
        lines.append("\n---\n")
        lines.append("## 延伸探索\n")
        for idx, question in enumerate(recommended_questions, 1):
            lines.append(f"{idx}. {question}")

    final_answer = "\n".join(lines)

    # [FIX 2025-12-04] 提取图片引用
    image_references = []
    if documents_view:
        for doc in documents_view:
            for image in doc.get("images", []):
                image_references.append({
                    **image,
                    "doc_name": doc.get("doc_name"),
                })

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

    # [FIX P0-Step1] 使用 MediArch Graph 的 add_items_with_dedup 替代简单去重
    # 这样可以保留 MongoDB 的 rich citations (positions, pdf_url, file_path)
    from backend.app.agents.base_agent import add_items_with_dedup
    unique_items = add_items_with_dedup([], all_items)

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

    # ✅ [FIX 2026-01-12] 移除内部实现细节，改为用户友好的提示
    notes = []
    if len(unique_items) == 0:
        notes.append("未找到相关结果，建议调整查询条件")
    elif len(unique_items) < 3:
        notes.append("检索结果较少，建议扩大查询范围或使用在线搜索")

    return {
        "aggregated_items": unique_items,
        "notes": notes,
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


_IMAGE_QUERY_TRIGGERS = (
    "平面图",
    "剖面图",
    "立面图",
    "总平面",
    "图纸",
    "图示",
    "示意图",
    "流程图",
    "结构图",
    "配图",
    "附图",
    "带图",
    "带图片",
    "给图",
    "看图",
    "图片",
    "image",
    "figure",
    "diagram",
    "plan",
    "section",
)

_IMAGE_QUERY_NEGATIONS = (
    "不要图",
    "不需要图",
    "不看图",
    "不要图片",
    "不需要图片",
)


def _wants_images(query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    if any(neg in q for neg in _IMAGE_QUERY_NEGATIONS):
        return False
    return True


def _is_image_item(item: AgentItem) -> bool:
    attrs = item.attrs or {}
    content_type = str(attrs.get("content_type") or "").lower()
    if content_type == "image" or attrs.get("image_url"):
        return True

    snippet = (item.snippet or "").strip()
    if snippet.startswith("[图片"):
        return True

    citations = item.citations or []
    if citations:
        image_hits = sum(
            1
            for c in citations
            if c.get("image_url") or str(c.get("content_type") or "").lower() == "image"
        )
        if image_hits == len(citations):
            return True

    return False


def _filter_text_items(items: List[AgentItem], query: str) -> List[AgentItem]:
    if not items:
        return []
    filtered = [item for item in items if not _is_image_item(item)]
    return filtered or items


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


def _normalize_inline_citation_groups(text: str) -> str:
    """
    将 `[1,2]` / `[1，2]` / `[1、2]` 等合并写法，规范化为前端可解析的 `[1][2]` 形式。
    """
    if not text:
        return text

    def _repl(match: re.Match) -> str:
        raw = match.group(1)
        nums = re.findall(r"\d+", raw or "")
        return "".join(f"[{n}]" for n in nums)

    return re.sub(r"\[(\d+(?:\s*[,，、]\s*\d+)+)\]", _repl, text)


def _expand_citation_ranges(text: str) -> str:
    """将范围引用展开为逐条引用，避免出现 [1-4] 形式。"""
    if not text:
        return text

    def _repl(match: re.Match) -> str:
        start = int(match.group(1))
        end = int(match.group(2))
        if start == end:
            return f"[{start}]"
        if start > end:
            start, end = end, start
        if end - start > 30:
            return f"[{start}][{end}]"
        return "".join(f"[{n}]" for n in range(start, end + 1))

    return re.sub(r"\[(\d+)\s*[-–—~～]\s*(\d+)\]", _repl, text)


_DECORATIVE_SYMBOLS_RE = re.compile(r"[\u2600-\u27BF\uFE0F\U0001F000-\U0001FAFF]")
_LEADING_BULLET_RE = re.compile(r"(?m)^\s*[•·●◦▪■]\s+")
_HEADING_LINE_RE = re.compile(r"^\s*#{1,6}\s+")


def _strip_decorative_symbols(text: str) -> str:
    """移除装饰性符号与 emoji（保留正文标点与单位）。"""
    if not text:
        return text
    text = _DECORATIVE_SYMBOLS_RE.sub("", text)
    text = _LEADING_BULLET_RE.sub("- ", text)
    return text


def _split_heading_lines(text: str) -> str:
    """确保标题独立成行，避免标题与正文混在同一行。"""
    if not text:
        return text

    lines = text.split("\n")
    out: List[str] = []

    for line in lines:
        match = re.match(r"^\s*(#{1,6})\s+(.+)$", line)
        if not match:
            out.append(line)
            continue

        marker = match.group(1)
        content = match.group(2).strip()
        split_match = re.split(r"[:：]", content, maxsplit=1)
        if len(split_match) == 2:
            heading = split_match[0].strip()
            body = split_match[1].strip()
            out.append(f"{marker} {heading}")
            if body:
                out.append("")
                out.append(body)
            continue

        out.append(line)

    return "\n".join(out)


def _attach_citations_to_sentence(text: str, citations: str) -> str:
    if not citations:
        return text
    if not text.strip():
        return citations
    match = re.search(r"[。！？.!?；;]", text)
    if match:
        idx = match.end()
        return f"{text[:idx]}{citations}{text[idx:]}"
    return f"{text.rstrip()}{citations}"


def _relocate_heading_citations(text: str) -> str:
    """移除标题中的引用，并附加到下一条正文句末。"""
    if not text:
        return text

    lines = text.split("\n")
    out: List[str] = []
    pending = ""

    for line in lines:
        stripped = line.strip()
        if _HEADING_LINE_RE.match(stripped):
            nums = re.findall(r"\[(\d+)\]", line)
            if nums:
                pending += "".join(f"[{n}]" for n in nums)
                line = re.sub(r"\[(\d+)\]", "", line).rstrip()
            out.append(line)
            continue

        if pending and stripped and not _HEADING_LINE_RE.match(stripped):
            line = _attach_citations_to_sentence(line, pending)
            pending = ""

        out.append(line)

    if pending:
        out.append(pending)

    return "\n".join(out)


def _relocate_leading_citations(text: str) -> str:
    """把段首/分点开头的引用移到该句末尾。"""
    if not text:
        return text

    parts = _split_fenced_code_blocks(text)
    if not parts:
        return text

    for part in parts:
        if part["type"] != "text":
            continue

        lines = part["value"].split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or _HEADING_LINE_RE.match(stripped):
                continue

            list_match = re.match(r"^(\s*(?:[-*+]|(\d+)[.、])\s+)(\[\d+\]\s*)+(.*)$", line)
            if list_match:
                prefix = list_match.group(1)
                rest = list_match.group(4)
                nums = re.findall(r"\[(\d+)\]", line)
                citations = "".join(f"[{n}]" for n in nums)
                if citations:
                    lines[i] = f"{prefix}{_attach_citations_to_sentence(rest, citations)}"
                continue

            lead_match = re.match(r"^(\s*)(\[\d+\]\s*)+(.*)$", line)
            if lead_match:
                nums = re.findall(r"\[(\d+)\]", line)
                citations = "".join(f"[{n}]" for n in nums)
                remainder = lead_match.group(3)
                lines[i] = f"{lead_match.group(1)}{_attach_citations_to_sentence(remainder, citations)}"

        part["value"] = "\n".join(lines)

    return "".join(p["value"] for p in parts)


def _strip_citations_in_tables(text: str) -> str:
    """移除表格单元格内的引用，并将引用移到表格后的注释行。"""
    if not text:
        return text

    def _is_table_row(line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        if "|" not in stripped:
            return False
        return stripped.startswith("|") or stripped.endswith("|") or stripped.count("|") >= 2

    def _is_separator(line: str) -> bool:
        stripped = line.strip()
        return bool(re.match(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?$", stripped))

    parts = _split_fenced_code_blocks(text)
    if not parts:
        return text

    for part in parts:
        if part["type"] != "text":
            continue

        lines = part["value"].split("\n")
        out: List[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if _is_table_row(line) and i + 1 < len(lines) and _is_separator(lines[i + 1]):
                table_lines = [line, lines[i + 1]]
                removed: List[int] = []
                i += 2
                while i < len(lines) and _is_table_row(lines[i]):
                    row = lines[i]
                    removed.extend(int(n) for n in re.findall(r"\[(\d+)\]", row))
                    cleaned = re.sub(r"\[(\d+)\]", "", row)
                    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
                    table_lines.append(cleaned)
                    i += 1
                out.extend(table_lines)
                if removed:
                    seen: set[int] = set()
                    ordered = [n for n in removed if not (n in seen or seen.add(n))]
                    note = f"注：表格数据来源见相关说明。{''.join(f'[{n}]' for n in ordered)}"
                    out.append("")
                    out.append(note)
                    out.append("")
                continue
            out.append(line)
            i += 1

        part["value"] = "\n".join(out)

    return "".join(p["value"] for p in parts)


def _split_fenced_code_blocks(text: str) -> List[Dict[str, str]]:
    """将文本切分为 code/non-code 段，避免对 ``` code ``` 内做引用重排。"""
    parts: List[Dict[str, str]] = []
    if not text:
        return parts

    last = 0
    for m in re.finditer(r"```[\s\S]*?```", text):
        if m.start() > last:
            parts.append({"type": "text", "value": text[last:m.start()]})
        parts.append({"type": "code", "value": m.group(0)})
        last = m.end()

    if last < len(text):
        parts.append({"type": "text", "value": text[last:]})

    return parts


def _ensure_all_citations_mentioned(text: str, citations_count: int) -> str:
    """
    如果 citations_count > 0，确保正文至少出现一次 `[1]..[N]`：
    - 仅在缺失时补齐
    - 补齐位置：第一段末尾（更符合“总结段落一次性标注”）
    - 跳过 fenced code blocks
    """
    if not text or citations_count <= 0:
        return text

    used: set[int] = set()
    for m in re.finditer(r"\[(\d+)\]", text):
        n = int(m.group(1))
        if 1 <= n <= citations_count:
            used.add(n)

    missing = [n for n in range(1, citations_count + 1) if n not in used]
    if not missing:
        return text

    inject = "".join(f"[{n}]" for n in missing)

    parts = _split_fenced_code_blocks(text)
    if not parts:
        return f"{text.rstrip()}{inject}"

    for part in parts:
        if part["type"] != "text":
            continue

        seg = part["value"]
        m = re.search(r"\n{2,}", seg)
        if m:
            head = seg[: m.start()].rstrip()
            tail = seg[m.start() :]
            part["value"] = f"{head}{inject}{tail}"
        else:
            part["value"] = f"{seg.rstrip()}{inject}"
        break

    return "".join(p["value"] for p in parts)


def _compact_citations_in_block(block: str) -> str:
    """
    段落/列表块级引用压缩：
    - 收集块内所有 `[n]`
    - 移除块内散落的 `[n]`
    - 在块末尾追加去重后的 `[n]`（保持出现顺序）
    """
    if not block:
        return block

    # 兼容：引用被单独换行时，先合并到上一行，避免产生空行破坏列表结构
    block = re.sub(r"\n+\s*(\[\d+\])", r"\1", block)

    citation_nums: List[int] = []
    seen: set[int] = set()
    for m in re.finditer(r"\[(\d+)\]", block):
        n = int(m.group(1))
        if n not in seen:
            seen.add(n)
            citation_nums.append(n)

    if not citation_nums:
        return block

    # 移除块内所有引用标记（保留换行，避免破坏列表）
    cleaned = re.sub(r"\[(\d+)\]", "", block)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = cleaned.rstrip()

    tail = "".join(f"[{n}]" for n in citation_nums)
    if not cleaned:
        return tail

    return f"{cleaned}{tail}"


def _compact_citations_paragraph_level(text: str) -> str:
    """
    将引用从“每句话/每分点后面”压缩为“每段/每个列表块末尾一次”。
    - 以空行（\\n\\n+）划分块，保持与前端段落解析一致
    - 跳过 fenced code blocks
    """
    if not text:
        return text

    parts = _split_fenced_code_blocks(text)
    if not parts:
        return text

    for part in parts:
        if part["type"] != "text":
            continue

        seg = part["value"]
        out: List[str] = []
        last = 0
        for m in re.finditer(r"\n{2,}", seg):
            block = seg[last:m.start()]
            sep = m.group(0)
            out.append(_compact_citations_in_block(block))
            out.append(sep)
            last = m.end()
        out.append(_compact_citations_in_block(seg[last:]))
        part["value"] = "".join(out)

    return "".join(p["value"] for p in parts)


def _tighten_citation_spacing(text: str) -> str:
    """清理引用之间的多余空白（例如把 `[1] [2]` 变成 `[1][2]`）。"""
    if not text:
        return text
    text = re.sub(r"\]\s*\n\s*\[", "][", text)
    text = re.sub(r"\]\s+\[", "][", text)
    return text


def _sort_adjacent_citation_groups(text: str) -> str:
    """
    将连续引用组（例如 `[3][1][2]` 或 `[3] [1] [2]`）排序为升序并去重。
    仅处理相邻引用，避免影响正文结构。
    """
    if not text:
        return text

    def _repl(match: re.Match) -> str:
        nums = [int(n) for n in re.findall(r"\[(\d+)\]", match.group(0))]
        ordered = sorted(dict.fromkeys(nums))  # preserve uniqueness while sorting
        return "".join(f"[{n}]" for n in ordered)

    # 允许组内有空白，但不跨越非空白字符
    return re.sub(r"(?:\[\d+\]\s*){2,}", _repl, text)


def _convert_consecutive_citations_to_range(text: str) -> str:
    """
    [NEW 2026-01-17] 将连续的引用标记转换为范围格式
    - [1][2][3][4][5] → [1-5]
    - [1][2][3] → [1-3]
    - [1][3][5] → [1][3][5]（不连续，保持原样）

    关键逻辑：
    1. 检测连续的 [n] 标记（允许中间有空格）
    2. 判断数字是否连续（差值为1）
    3. 如果连续且数量≥3，转换为范围格式
    4. 如果不连续或数量<3，保持原样
    """
    if not text:
        return text

    def _repl(match: re.Match) -> str:
        # 提取所有数字
        nums = [int(n) for n in re.findall(r"\[(\d+)\]", match.group(0))]
        if not nums:
            return match.group(0)

        # 去重并排序
        nums = sorted(set(nums))

        # 如果只有1-2个引用，保持原样
        if len(nums) <= 2:
            return "".join(f"[{n}]" for n in nums)

        # 检查是否连续
        is_consecutive = all(nums[i+1] - nums[i] == 1 for i in range(len(nums) - 1))

        if is_consecutive:
            # 连续引用：转换为范围格式
            return f"[{nums[0]}-{nums[-1]}]"
        else:
            # 不连续引用：保持原样
            return "".join(f"[{n}]" for n in nums)

    # 匹配连续的 [n] 标记（允许中间有空格）
    return re.sub(r"(?:\[\d+\]\s*){2,}", _repl, text)



def _remap_citations_by_first_appearance(
    answer: str,
    citations: List[Dict[str, Any]],
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    让“正文引用编号”与“侧边PDF/参考资料列表”的顺序更符合阅读直觉：
    - 以正文中引用首次出现顺序重新编号（第一处出现 -> [1]）
    - 同步重排 citations 列表，使 `[n]` 始终指向正确资料
    """
    if not answer or not citations:
        return answer, citations

    max_idx = len(citations)
    order: List[int] = []
    seen: set[int] = set()

    for m in re.finditer(r"\[(\d+)\]", answer):
        n = int(m.group(1))
        if 1 <= n <= max_idx and n not in seen:
            seen.add(n)
            order.append(n)

    if not order:
        return answer, citations

    remap = {old: new for new, old in enumerate(order, start=1)}

    def _repl(m: re.Match) -> str:
        old = int(m.group(1))
        new = remap.get(old)
        # out-of-range / unmapped citations should not exist after前置过滤；兜底直接移除
        return f"[{new}]" if new is not None else ""

    new_answer = re.sub(r"\[(\d+)\]", _repl, answer)
    new_citations = [citations[i - 1] for i in order if 1 <= i <= max_idx]

    return new_answer, new_citations


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
    request = state.get("request")
    wants_images = _wants_images(query)
    text_items = _filter_text_items(aggregated_items, query)
    image_items_count = sum(1 for item in aggregated_items if _is_image_item(item))
    document_views = _build_document_views(text_items)

    # ✅ [2025-11-25] 从 state 获取 Knowledge Fusion 输出
    answer_graph_data = state.get("answer_graph_data", {})
    unified_hints = state.get("unified_hints", {})

    # ✅ [2025-11-25] 从 request.metadata 获取 answer_graph_data（兼容旧版）
    if not answer_graph_data and request and request.metadata:
        answer_graph_data = request.metadata.get("answer_graph_data", {})
        unified_hints = request.metadata.get("unified_hints", {})

    strict_cross_doc = _is_strict_cross_doc_request(query, request)

    logger.info(
        "[Synthesizer→Synthesize] 合成答案，共 %d 条结果（文本=%d，图片=%d，retry=%d）",
        len(aggregated_items),
        len(text_items),
        image_items_count,
        retry_count,
    )

    # 如果有反馈，记录
    if feedback_message:
        logger.info(f"[Synthesizer→Synthesize] 使用反馈改进答案: {feedback_message}")

    # 无数据时返回兜底答案
    if not aggregated_items:
        logger.info("[Synthesizer→Synthesize] 无数据可用，返回默认提示")
        fallback = _build_rule_based_answer(query, text_items, notes, document_views)
        if not wants_images:
            fallback["image_references"] = []
        fallback.setdefault("final_citations", [])
        fallback.setdefault("answer_graph_data", answer_graph_data)
        fallback.setdefault("unified_hints", unified_hints)
        fallback.setdefault("strict_cross_doc", strict_cross_doc)
        fallback.setdefault("strict_citations_candidate_count", 0)
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
                            # [FIX P0-Step2] 补全 MongoDB citations 的所有关键字段
                            # 确保 positions, pdf_url, file_path 等精确引用信息不丢失
                            mongodb_citations.append({
                                "source": citation.get("source", ""),
                                "location": location,
                                "snippet": citation.get("snippet", "")[:100],
                                # 新增：精确定位字段
                                "positions": citation.get("positions", []),
                                "pdf_url": citation.get("pdf_url"),
                                "file_path": citation.get("file_path"),
                                "document_path": citation.get("document_path"),
                                "page_number": citation.get("page_number"),
                                "page_range": citation.get("page_range"),
                                "chapter": citation.get("chapter"),
                                "chapter_title": citation.get("chapter_title"),
                                "sub_section": citation.get("sub_section"),
                                "content_type": citation.get("content_type", "text"),
                                "chunk_id": citation.get("chunk_id"),
                                "doc_id": citation.get("doc_id"),
                                "highlight_text": citation.get("highlight_text", ""),
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
                                    "positions": citation.get("positions", []),
                                    "pdf_url": citation.get("pdf_url"),
                                    "file_path": citation.get("file_path"),
                                    "document_path": citation.get("document_path"),
                                    "page_number": citation.get("page_number"),
                                    "page_range": citation.get("page_range"),
                                    "chapter": citation.get("chapter"),
                                    "chapter_title": citation.get("chapter_title"),
                                    "sub_section": citation.get("sub_section"),
                                    "content_type": citation.get("content_type", "image"),
                                    "chunk_id": citation.get("chunk_id"),
                                    "doc_id": citation.get("doc_id"),
                                    "highlight_text": citation.get("highlight_text", ""),
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
    for doc in document_views:
        for image in doc.get("images", []):
            annotated = dict(image)
            annotated["doc_name"] = doc.get("doc_name")
            document_images.append(annotated)

    if wants_images:
        if document_images:
            merged_images: List[Dict[str, Any]] = []
            seen_urls: set[str] = set()
            for img in list(image_citations) + list(document_images):
                url = str(img.get("image_url") or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                merged_images.append(img)
            image_citations = merged_images
    else:
        image_citations = []

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

    # ============================================================================
    # [FIX 2026-01-14] 构建"最终 citations"（用于：LLM 严格对齐 [n] + API/前端点击一致）
    # - 仅保留文本 citations（图片通过 [image:i] 机制单独处理）
    # - 支持跨章节引用：同一资料的不同章节/页码视为不同引用
    # - 提高引用限额到 50，确保支持10+份资料，每份资料多个页码
    # ============================================================================
    max_citations = 50
    try:
        if isinstance(request, AgentRequest):
            max_citations = int((request.metadata or {}).get("max_citations") or 50)
    except Exception:
        max_citations = 50

    from backend.app.utils.citation_builder import normalize_citations

    strict_citations_candidate_count = 0
    if strict_cross_doc:
        final_citations = _build_balanced_citations_for_strict_cross_doc(
            aggregated_items,
            request,
            max_citations=max_citations,
        )
        strict_citations_candidate_count = len(final_citations)
    else:
        def _cite_score(c: Dict[str, Any]) -> int:
            """
            [FIX 2026-01-13] 优化引用评分权重，确保跨文档关联质量

            评分策略：
            - PDF 可预览性（1000分）：优先展示有 PDF 的引用
            - 精确定位能力（100分）：positions 字段支持黄色高亮
            - 文本类型（10分）：文本优于图片（图片通过 [image:i] 单独处理）
            """
            content_type = str(c.get("content_type") or "").lower()
            is_image = bool(c.get("image_url")) or content_type == "image"
            is_text = not is_image
            has_positions = bool(c.get("positions"))
            has_pdf = bool(c.get("pdf_url")) or bool(c.get("document_path")) or bool(c.get("file_path"))

            # 新权重：PDF(1000) > positions(100) > text(10)
            return (1000 if has_pdf else 0) + (100 if has_positions else 0) + (10 if is_text else 0)

        # [FIX 2026-01-14] 优化去重逻辑：更宽松的去重策略
        # 关键改进：
        # 1. 如果 chunk_id 为空，使用 (doc_id, page_num) 作为key（允许同一页的不同chunk）
        # 2. 优先保留有 PDF URL 和 positions 的引用
        # 3. 记录去重统计，便于调试
        # 4. 目标：保留10+条有效引用
        citation_best: Dict[tuple, Dict[str, Any]] = {}
        citation_order: List[tuple] = []

        # 统计：有多少个item有citations
        items_with_citations = 0
        total_citations_found = 0
        skipped_no_source = 0
        skipped_image = 0

        for item in aggregated_items:
            if item.citations and len(item.citations) > 0:
                items_with_citations += 1
                total_citations_found += len(item.citations)

            for cite in (item.citations or []):
                data = _citation_to_dict(cite)
                if not data or not data.get("source"):
                    skipped_no_source += 1
                    continue
                # 跳过图片（图片通过 [image:i] 机制单独处理）
                if data.get("image_url") or str(data.get("content_type") or "").lower() == "image":
                    skipped_image += 1
                    continue

                # Composite key: 优化策略
                doc_id = str(data.get("doc_id") or data.get("source") or "").strip()
                page_num = data.get("page_number")
                chunk_id = str(data.get("chunk_id") or "").strip()

                if not doc_id:
                    skipped_no_source += 1
                    continue

                # [FIX 2026-01-14] 更宽松的去重策略：
                # - 如果有 chunk_id，使用 (doc_id, page_num, chunk_id)
                # - 如果没有 chunk_id，使用 (doc_id, page_num, content_type, snippet_hash)
                #   这样同一页的不同内容可以有多个引用
                # - 特别处理：图片和文字即使在同一页也应该分开显示
                content_type = str(data.get("content_type") or "text").lower()
                if chunk_id:
                    cite_key = (doc_id, page_num, chunk_id)
                else:
                    # 使用 content_type + snippet 的前50个字符作为区分
                    snippet_hash = str(data.get("snippet", ""))[:50]
                    cite_key = (doc_id, page_num, content_type, snippet_hash)

                if cite_key not in citation_best:
                    citation_order.append(cite_key)
                    citation_best[cite_key] = data
                    continue

                # 如果重复，保留分数更高的
                if _cite_score(data) > _cite_score(citation_best[cite_key]):
                    citation_best[cite_key] = data

        # [FIX 2026-01-14] 调试日志：追踪citations的来源
        logger.info(
            f"[Synthesizer→Citations] 统计：{len(aggregated_items)} 个items，"
            f"{items_with_citations} 个有citations，"
            f"共 {total_citations_found} 条原始citations，"
            f"跳过 {skipped_no_source} 条（无source），"
            f"跳过 {skipped_image} 条（图片），"
            f"去重后 {len(citation_best)} 条"
        )

        final_citations = normalize_citations([citation_best[k] for k in citation_order][:max_citations])

    # [FIX 2026-01-14] 验证final_citations的PDF URL完整性
    citations_with_pdf = sum(1 for c in final_citations if c.get("pdf_url") or c.get("file_path") or c.get("document_path"))
    citations_without_pdf = len(final_citations) - citations_with_pdf
    if citations_without_pdf > 0:
        logger.warning(
            f"[Synthesizer→Citations] 警告：{citations_without_pdf}/{len(final_citations)} 条引用缺少PDF URL，"
            f"这些引用在前端无法预览PDF"
        )

    # [FIX 2026-01-14] 关键修复：确保 citations_catalog 包含足够多的引用
    # 问题：LLM看到了 document_citations (10条) 和 attribute_citations (10条)，
    # 但 final_citations 可能只有4条，导致LLM生成 [5][6][7][8] 等无效引用
    #
    # 解决方案：
    # 1. 记录 final_citations 的实际数量
    # 2. 在 System Prompt 中明确告知LLM只能使用 [1] 到 [N] 的引用
    # 3. 如果 final_citations 少于10条，记录警告

    citations_count = len(final_citations)
    citations_catalog = _format_citations_catalog(final_citations, max_snippet_chars=180) if final_citations else ""

    # 警告：如果引用数量不足
    if citations_count < 10:
        logger.warning(
            f"[Synthesizer→Citations] 引用数量不足：只有 {citations_count} 条引用，"
            f"但用户期望10+条。可能原因：去重过度、检索结果不足、或citations字段缺失"
        )

    enhanced_context = {
        "query": query,
        "total_results": len(aggregated_items),
        "knowledge_graph": neo4j_query_path,  # 知识图谱路径
        "document_citations": mongodb_citations[:10],  # MongoDB引用（增加到10个）
        "attribute_citations": milvus_citations[:10],  # Milvus引用（增加到10个）
        "online_supplements": online_search_results,  # 在线补充（5-10条，已排序）
        "related_images": image_citations[:10] if wants_images else [],  # ✅ [NEW] 相关图片（增加到10个）
        "knowledge_graph_citations": neo4j_citations[:10],  # ✅ [NEW] Neo4j来源引用
        "documents_view": top_documents,
        "doc_roles": doc_roles,
        "doc_distribution": doc_distribution,
        "documents_total": len(document_views),
        "items_summary": [],
        "key_takeaways": [],
        "unified_hints": unified_hints if unified_hints else None,
        "citations_catalog": citations_catalog,
        "citations_count": citations_count,  # [FIX] 使用实际数量
    }

    # 提取items的简要信息
    top_items = text_items[:6]
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
            "citations_count": len(item.citations or []),
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
    documents_dir = Path(
        os.getenv("DATA_PROCESS_DOCUMENTS_DIR", str(project_root / "data_process" / "documents"))
    ).resolve()

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
                normalized = str(file_path or "").replace("\\", "/")
                marker = "documents/"
                idx = normalized.lower().find(marker)
                rel_path = normalized[idx + len(marker) :].lstrip("/") if idx >= 0 else (rel_path or None)

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

    for citation in image_citations:
        _attach_pdf_metadata(citation)

    for image in document_images:
        _attach_pdf_metadata(image)

    # [FIX 2026-01-14] 关键修复：确保 final_citations 也有 PDF metadata
    for citation in final_citations:
        _attach_pdf_metadata(citation)

    # ============================================================================
    # [FIX 2026-01-17] 全面优化 System Prompt：解决视觉拥挤、引用干扰、规范模糊三大问题
    # [UPDATE 2026-01-17-v2] 调整策略：全面详细、图片嵌入段落、保持专业深度
    # ============================================================================
    system_prompt = """你是 MediArch 综合医院建筑顾问。你的任务是生成全面、详细、专业的答案。

## 核心原则（必须严格遵守）
1. 内容全面详细、学术严谨，基于检索资料展开
2. 结构清晰：必须提供“目录”，并使用 Markdown 分级标题与分点
3. 标题与正文分行：标题单独成行，正文另起一行
4. 核心数据表格化：涉及尺寸、间距、配比、流程指标等信息时必须用 Markdown 表格呈现
5. 图文并茂：若存在 `related_images`，应尽可能全部嵌入（`[image:i]`），放在相关段落下方并配斜体说明
6. 引用精准投放：引用标记紧跟被支持的具体句子之后

## 输出结构（灵活但有目录）
- 开头输出 `## 目录`，列出 3-6 个将要展开的 `##` 标题
- 正文使用 `##` 作为主标题，`###` 作为子标题；标题名称根据问题动态命名
- 每个 `##` 下至少 1 个 `###`；每个 `###` 下至少 2 条分点
- 标题后换行再写正文
- 表格前后留空行；表格如需引用，在表格后单独写“注：...”并标注引用
- 图片用 `[image:i]` 嵌入相关段落下方，并使用斜体说明（不超过40字）
- Markdown 列表仅使用 `-` 或 `1.`，不要使用装饰性符号

## 引用规范（核心 - 必须严格遵守）
**可用引用索引范围：仅 [1] 到 [{citations_count}]**

1. 引用必须紧跟该句末尾，不要出现在标题或段落开头
2. 不使用范围写法：禁止 [1-4]、[1–4]、[1—4]
3. 多个引用使用连续标记：[1][2][5]（不要加空格）
4. 表格内禁止引用；在表格后用一行注释标注引用
5. 不要在图片说明中添加引用索引

## 文本风格
- 去除装饰性符号与图标，不使用 emoji 或花哨分隔线
- 只输出学术化、严谨、可直接用于文档的正文内容

## 图片使用规范
- 默认图文并茂：如有图片则全部展示
- 图片索引来自 `related_images`（从 0 开始），不要虚构
- 图片标记 `[image:i]` 单独一行
- 图片说明使用斜体 `*图1：...*`，不超过40字

## 严格禁止的内容
- 内部信息、评分、调试字段
- 参考资料章节（系统自动生成）
- 表格单元格内的引用
- `| :--- | :--- |` 对齐语法（只用 `|---|---|`）
"""

    if feedback_message:
        system_prompt = (
            system_prompt
            + "\n\n## 改进要求\n"
            + f"{feedback_message}\n"
        )

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
        # [FIX 2026-01-14] 关键修复：移除LLM生成的无效引用
        # 问题：LLM可能生成 [5][6][7][8] 等超出 citations_count 范围的引用
        # 解决方案：后处理验证，移除所有超出范围的引用标记
        # ============================================================================

        logger.info(f"[Synthesizer→PostProcess] 开始后处理验证，citations_count={citations_count}")

        # 1. 检测无效引用
        invalid_citations = []
        citation_pattern = re.compile(r'\[(\d+)\]')
        all_citations_in_answer = []

        for match in citation_pattern.finditer(final_answer):
            cite_num = int(match.group(1))
            all_citations_in_answer.append(cite_num)
            if cite_num > citations_count:
                invalid_citations.append(cite_num)

        logger.info(
            f"[Synthesizer→PostProcess] 答案中的所有引用: {sorted(set(all_citations_in_answer))}，"
            f"无效引用: {sorted(set(invalid_citations))}"
        )

        # 2. 如果发现无效引用，记录警告并移除
        if invalid_citations:
            unique_invalid = sorted(set(invalid_citations))
            logger.warning(
                f"[Synthesizer→InvalidCitations] LLM生成了无效引用: {unique_invalid}，"
                f"但 citations_count 只有 {citations_count}。将移除这些无效引用。"
            )

            # 移除无效引用（保留有效引用）
            def replace_invalid_citation(match):
                cite_num = int(match.group(1))
                if cite_num > citations_count:
                    logger.debug(f"[Synthesizer→PostProcess] 移除无效引用 [{cite_num}]")
                    return ""  # 移除无效引用
                return match.group(0)  # 保留有效引用

            final_answer = citation_pattern.sub(replace_invalid_citation, final_answer)

            # 验证：检查是否还有无效引用
            remaining_invalid = []
            for match in citation_pattern.finditer(final_answer):
                cite_num = int(match.group(1))
                if cite_num > citations_count:
                    remaining_invalid.append(cite_num)

            if remaining_invalid:
                logger.error(
                    f"[Synthesizer→PostProcess] 错误：移除后仍有无效引用 {remaining_invalid}！"
                )
            else:
                logger.info(
                    f"[Synthesizer→PostProcess] 成功移除所有无效引用，"
                    f"剩余有效引用: {sorted(set([int(m.group(1)) for m in citation_pattern.finditer(final_answer)]))}"
                )
        else:
            logger.info(f"[Synthesizer→PostProcess] 未发现无效引用，所有引用都在有效范围内")

        # ============================================================================
        # [FIX 2026-01-13] 文本清洗：移除引用前的换行符和系统干扰符号
        # ============================================================================
        # 1. 移除引用符号前的换行符（避免 `\n[1]` 导致前端解析为列表）
        final_answer = re.sub(r'\n+\s*(\[\d+\])', r'\1', final_answer)

        # 2. 移除系统级索引标记（如 `+1`、`🔗` 等）
        final_answer = re.sub(r'\+\d+', '', final_answer)
        final_answer = re.sub(r'🔗', '', final_answer)

        # 3. 清理多余空格
        final_answer = re.sub(r' +', ' ', final_answer)

        # 4. 清理多余换行（保留段落间距）
        final_answer = re.sub(r'\n{3,}', '\n\n', final_answer)

        # 5. 移除装饰性符号
        final_answer = _strip_decorative_symbols(final_answer)
        final_answer = _split_heading_lines(final_answer)

        # ============================================================================
        # [FIX 2026-01-20] 引用位置校准 + 结构清理
        # - 规范化引用格式（不使用范围）
        # - 标题/表格内移除引用，并转移至正文句末
        # - 重新编号：按"正文首次出现顺序"从 [1] 开始编号，并同步重排 citations 列表
        # ============================================================================
        if citations_count > 0:
            before_answer = final_answer
            before_count = len(final_citations) if isinstance(final_citations, list) else 0
            final_answer = _normalize_inline_citation_groups(final_answer)
            final_answer = _expand_citation_ranges(final_answer)
            final_answer = _relocate_heading_citations(final_answer)
            final_answer = _strip_citations_in_tables(final_answer)
            final_answer = _relocate_leading_citations(final_answer)
            final_answer = _tighten_citation_spacing(final_answer)
            final_answer, final_citations = _remap_citations_by_first_appearance(final_answer, final_citations)
            final_answer = _sort_adjacent_citation_groups(final_answer)
            final_answer = _tighten_citation_spacing(final_answer)
            after_count = len(final_citations) if isinstance(final_citations, list) else 0

            if final_answer != before_answer or after_count != before_count:
                logger.info(
                    "[Synthesizer→PostProcess] 已应用引用校准/重排（citations=%d→%d）",
                    before_count,
                    after_count,
                )

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
            "answer_graph_data": answer_graph_data,
            "unified_hints": unified_hints,
            "image_references": image_citations if wants_images and image_citations else [],
            "final_citations": final_citations,
            "strict_cross_doc": strict_cross_doc,
            "strict_citations_candidate_count": strict_citations_candidate_count,
            "document_citations": {
                "mongodb": mongodb_citations[:10],
                "milvus": milvus_citations[:10],
                "neo4j": neo4j_citations[:10],
            },
        }

    except Exception as err:
        logger.error(f"[Synthesizer→Synthesize] LLM 合成失败: {err}，使用规则兜底")
        fallback = _build_rule_based_answer(query, text_items, notes, document_views)
        fallback["notes"] = notes
        fallback["final_citations"] = final_citations
        fallback["strict_cross_doc"] = strict_cross_doc
        fallback["strict_citations_candidate_count"] = strict_citations_candidate_count
        if not wants_images:
            fallback["image_references"] = []
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
    
    system_prompt = (
        "你是医院建筑设计领域的质量评估专家。\n\n"
        "评估以下答案的质量：\n\n"
        f"查询：{query}\n"
        f"答案：{final_answer[:1500]}\n"
        f"引用数量：{citation_count}\n"
        f"数据来源：{sources}\n"
        f"结果条数：{len(aggregated_items)}\n\n"
        "评估标准：\n"
        "1. 信息完整性 - 是否完整回答了问题（权重 40%）\n"
        "2. 引用充分性 - 是否有足够的数据支持（权重 30%）\n"
        "3. 专业准确性 - 信息是否专业可靠（权重 30%）\n\n"
        '返回格式（必须是有效的 JSON）：\n'
        '{"quality_score": 0.85, "is_quality_good": true, "feedback": "建议补充急诊部的具体面积要求"}'
    )
    
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
    构建 Synthesizer 图 
    """
    builder = StateGraph(SynthesizerState)

    # 添加节点
    builder.add_node("aggregate", node_aggregate)
    builder.add_node("synthesize", node_synthesize)
    builder.add_node("evaluate_quality", node_evaluate_quality)
    builder.add_node("request_retry", node_request_retry)
    builder.add_node("finalize", node_finalize)
    builder.add_node("finalize_with_warning", node_finalize_with_warning)

    # 设置流程：聚合 → 合成 → 质量评估 → 最终化/重试
    builder.set_entry_point("aggregate")
    builder.add_edge("aggregate", "synthesize")
    builder.add_edge("synthesize", "evaluate_quality")
    builder.add_conditional_edges(
        "evaluate_quality",
        route_after_evaluation,
        {
            "finalize": "finalize",
            "request_retry": "request_retry",
            "finalize_with_warning": "finalize_with_warning",
        },
    )
    builder.add_edge("request_retry", "synthesize")
    builder.add_edge("finalize", END)
    builder.add_edge("finalize_with_warning", END)

    logger.info("[Synthesizer] 图构建完成")

    return builder.compile()


# ============================================================================
# 导出图（供 LangGraph Studio 使用）
# ============================================================================

# ✅ 使用纯函数构建（删除 BaseAgent 类）
graph = build_synthesizer_graph()

logger.info("[Synthesizer] 图已导出")
