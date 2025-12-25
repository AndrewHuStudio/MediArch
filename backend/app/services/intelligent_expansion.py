"""
智能概念扩展服务

基于LLM的动态概念发现系统，替代静态同义词字典。
为医院建筑设计领域提供智能化的查询词扩展。

作者：Claude Code
日期：2025-01-20
"""

from __future__ import annotations

import os
import asyncio
import logging
import json
import re
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

from langchain.chat_models import init_chat_model
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger(__name__)


class ConceptExpansionResult(BaseModel):
    """概念扩展结果"""

    original_term: str = Field(..., description="原始查询词")
    related_concepts: List[str] = Field(default_factory=list, description="相关概念列表")
    synonyms: List[str] = Field(default_factory=list, description="同义词列表")
    broader_terms: List[str] = Field(default_factory=list, description="上位概念")
    narrower_terms: List[str] = Field(default_factory=list, description="下位概念")
    confidence_score: float = Field(default=0.8, description="扩展置信度")
    expansion_method: str = Field(default="llm", description="扩展方法")


# ============================================================================
# LLM 配置和管理
# ============================================================================

_expansion_llm = None
_llm_lock = asyncio.Lock()

# 简单LRU缓存，避免重复触发LLM
_concept_cache: Dict[str, List[str]] = {}
_cache_lock = asyncio.Lock()
_MAX_CACHE_SIZE = 128


async def get_expansion_llm():
    """获取概念扩展专用LLM"""
    global _expansion_llm

    if _expansion_llm is not None:
        return _expansion_llm

    async with _llm_lock:
        if _expansion_llm is not None:
            return _expansion_llm

        try:
            api_key = os.getenv("INTELLIGENT_EXPANSION_API_KEY") or os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("缺少 INTELLIGENT_EXPANSION_API_KEY 或 OPENAI_API_KEY")

            base_url = (os.getenv("INTELLIGENT_EXPANSION_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").rstrip("/")
            model_name = os.getenv("INTELLIGENT_EXPANSION_MODEL", "gpt-4o-mini")
            model_provider = os.getenv("INTELLIGENT_EXPANSION_PROVIDER") or os.getenv("OPENAI_MODEL_PROVIDER") or "openai"

            _expansion_llm = await asyncio.to_thread(
                init_chat_model,
                model=model_name,
                model_provider=model_provider,
                api_key=api_key,
                base_url=base_url,
                temperature=0.3,  # 稍微提高创造性，但保持准确性
                max_tokens=2000,
                timeout=60,  # [FIX 2025-12-04] 添加60秒超时
            )

            logger.info(f"[IntelligentExpansion] LLM初始化成功: {model_name}")
            return _expansion_llm

        except Exception as e:
            logger.error(f"[IntelligentExpansion] LLM初始化失败: {e}")
            raise


# ============================================================================
# 核心扩展函数
# ============================================================================

async def llm_expand_medical_concepts(
    query_term: str,
    context_domain: str = "医院建筑设计",
    max_concepts: int = 15
) -> ConceptExpansionResult:
    """
    使用LLM智能扩展医院建筑领域的概念

    Args:
        query_term: 原始查询词
        context_domain: 领域上下文
        max_concepts: 最大扩展概念数量

    Returns:
        ConceptExpansionResult: 扩展结果
    """
    try:
        llm = await get_expansion_llm()

        # 构建专业的prompt
        system_prompt = f"""
你是{context_domain}领域的资深专家。请为用户的查询词生成相关概念。

要求：
1. 仅限于{context_domain}领域的专业术语
2. 包括同义词、别名、简称、全称
3. 包括功能相近或概念相关的术语
4. 按相关度从高到低排序
5. 避免过于宽泛或无关的概念

返回JSON格式：
{{
    "synonyms": ["同义词1", "同义词2"],
    "related_concepts": ["相关概念1", "相关概念2"],
    "broader_terms": ["上位概念1"],
    "narrower_terms": ["下位概念1", "下位概念2"],
    "confidence": 0.85
}}

示例：
查询词：护理单元
{{
    "synonyms": ["病区", "护士站", "病房区"],
    "related_concepts": ["住院区", "护理区域", "病房护理", "护理工作站"],
    "broader_terms": ["住院部", "医疗功能区"],
    "narrower_terms": ["普通病房", "重症病房", "隔离病房"],
    "confidence": 0.9
}}
"""

        user_prompt = f"查询词：{query_term}"

        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ])

        content = (response.content or "").strip()
        if not content:
            logger.warning("[IntelligentExpansion] LLM响应为空，response=%r", response)

        # 解析JSON响应
        expansion_data = parse_llm_response(content)

        # 构建结果
        all_concepts = []
        result = ConceptExpansionResult(
            original_term=query_term,
            synonyms=expansion_data.get("synonyms", []),
            related_concepts=expansion_data.get("related_concepts", []),
            broader_terms=expansion_data.get("broader_terms", []),
            narrower_terms=expansion_data.get("narrower_terms", []),
            confidence_score=expansion_data.get("confidence", 0.8),
            expansion_method="llm"
        )

        # 合并所有概念并去重
        all_concepts.extend(result.synonyms)
        all_concepts.extend(result.related_concepts)
        all_concepts.extend(result.broader_terms)
        all_concepts.extend(result.narrower_terms)

        # 去重并过滤
        unique_concepts = []
        seen = {query_term.lower()}

        for concept in all_concepts:
            concept = concept.strip()
            if not concept or len(concept) < 2 or concept.lower() in seen:
                continue
            seen.add(concept.lower())
            unique_concepts.append(concept)

        # 限制数量
        if len(unique_concepts) > max_concepts:
            unique_concepts = unique_concepts[:max_concepts]

        logger.info(
            f"[IntelligentExpansion] '{query_term}' -> {len(unique_concepts)} 个概念"
        )

        return result

    except Exception as e:
        logger.warning(f"[IntelligentExpansion] LLM扩展失败: {e}")

        # Fallback到基础规则
        return fallback_expansion(query_term)


def parse_llm_response(content: str) -> Dict[str, Any]:
    """解析LLM的JSON响应"""
    # 检查空内容
    if not content or not content.strip():
        logger.warning("[IntelligentExpansion] LLM返回空内容，使用默认值")
        return {
            "related_concepts": [],
            "synonyms": [],
            "broader_terms": [],
            "narrower_terms": [],
            "confidence": 0.5
        }

    try:
        # 查找JSON块
        if "```json" in content:
            json_start = content.find("```json") + 7
            json_end = content.find("```", json_start)
            json_content = content[json_start:json_end].strip()
        elif "```" in content:
            # 处理没有指定语言的代码块
            json_start = content.find("```") + 3
            json_end = content.find("```", json_start)
            if json_end > json_start:
                json_content = content[json_start:json_end].strip()
            else:
                json_content = content
        else:
            # 直接查找JSON格式 - 使用贪婪匹配找到完整的JSON对象
            json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content, re.DOTALL)
            if json_match:
                json_content = json_match.group()
            else:
                json_content = content.strip()

        # 检查提取的内容是否为空
        if not json_content:
            logger.warning("[IntelligentExpansion] 未找到有效JSON内容")
            return {
                "related_concepts": [],
                "synonyms": [],
                "broader_terms": [],
                "narrower_terms": [],
                "confidence": 0.5
            }

        data = json.loads(json_content)
        return data

    except json.JSONDecodeError as e:
        logger.warning(f"[IntelligentExpansion] JSON解析失败: {e}, 内容: {content[:200]}...")

        # 尝试提取列表形式的概念
        concepts = re.findall(r'["""]([^"""]+)["""]', content)
        return {
            "related_concepts": concepts[:10] if concepts else [],
            "synonyms": [],
            "broader_terms": [],
            "narrower_terms": [],
            "confidence": 0.6
        }


def fallback_expansion(query_term: str) -> ConceptExpansionResult:
    """
    Fallback概念扩展（当LLM不可用时）

    使用基础的规则和医院建筑领域知识
    """
    # 基础医院建筑概念映射
    basic_mappings = {
        "护理单元": ["病区", "护士站", "病房区", "护理区"],
        "手术室": ["手术间", "洁净手术部", "手术区", "无菌手术室"],
        "门诊部": ["门诊区", "门诊科", "门诊楼", "门诊大厅"],
        "住院部": ["住院区", "住院楼", "病房楼", "住院病区"],
        "急诊科": ["急诊部", "急诊区", "急救科", "急诊大厅"],
        "医技科室": ["辅助科室", "医技部门", "检验科", "影像科"],
        "ICU": ["重症监护", "重症病房", "重症监护室", "危重监护"],
        "药房": ["药剂科", "药库", "药物配送", "药品储存"],
    }

    # 查找匹配的概念
    related_concepts = []
    for key, values in basic_mappings.items():
        if key in query_term or query_term in key:
            related_concepts.extend(values)
            break

    # 如果没有直接匹配，使用通用医院建筑术语
    if not related_concepts:
        general_terms = ["医疗空间", "功能分区", "建筑布局", "流线设计"]
        related_concepts = general_terms[:3]

    return ConceptExpansionResult(
        original_term=query_term,
        related_concepts=related_concepts[:8],
        synonyms=[],
        broader_terms=[],
        narrower_terms=[],
        confidence_score=0.6,
        expansion_method="fallback"
    )


# ============================================================================
# 整合函数
# ============================================================================

async def intelligent_concept_expansion(
    query_term: str,
    max_terms: int = 15,
    include_original: bool = True
) -> List[str]:
    """
    智能概念扩展：输出扩展后的搜索词列表

    这是替代静态同义词字典的主要接口函数

    Args:
        query_term: 原始查询词
        max_terms: 最大返回词汇数量
        include_original: 是否包含原始查询词

    Returns:
        List[str]: 扩展后的搜索词列表
    """
    cache_key = f"{query_term}|{max_terms}|{int(include_original)}"

    async with _cache_lock:
        cached = _concept_cache.get(cache_key)
    if cached is not None:
        logger.info(f"[IntelligentExpansion] '{query_term}' 命中缓存，返回 {len(cached)} 个术语")
        return list(cached)

    try:
        result = await llm_expand_medical_concepts(query_term, max_concepts=max_terms)

        # 合并所有概念
        expanded_terms = []
        if include_original:
            expanded_terms.append(query_term)

        # 按优先级添加概念
        expanded_terms.extend(result.synonyms[:5])  # 同义词优先级最高
        expanded_terms.extend(result.related_concepts[:8])  # 相关概念次之
        expanded_terms.extend(result.narrower_terms[:3])  # 下位概念
        expanded_terms.extend(result.broader_terms[:2])  # 上位概念最少

        # 去重并限制数量
        unique_terms = []
        seen = set()
        for term in expanded_terms:
            term = term.strip()
            if not term or term.lower() in seen:
                continue
            seen.add(term.lower())
            unique_terms.append(term)

        final_terms = unique_terms[:max_terms]

        logger.info(
            f"[IntelligentExpansion] '{query_term}' 智能扩展: "
            f"{len(final_terms)} 个术语, 置信度: {result.confidence_score:.2f}"
        )

        async with _cache_lock:
            if len(_concept_cache) >= _MAX_CACHE_SIZE:
                # 移除最早的缓存项
                _concept_cache.pop(next(iter(_concept_cache)))
            _concept_cache[cache_key] = list(final_terms)

        return final_terms

    except Exception as e:
        logger.error(f"[IntelligentExpansion] 智能扩展失败: {e}")

        # 最基本的fallback
        fallback_terms = [query_term] if include_original else []
        async with _cache_lock:
            if len(_concept_cache) >= _MAX_CACHE_SIZE:
                _concept_cache.pop(next(iter(_concept_cache)))
            _concept_cache[cache_key] = list(fallback_terms)
        return fallback_terms


# ============================================================================
# 兼容性接口
# ============================================================================

async def expand_medical_query_terms(
    query_terms: List[str],
    max_per_term: int = 8
) -> List[str]:
    """
    批量扩展多个查询词

    Args:
        query_terms: 原始查询词列表
        max_per_term: 每个词的最大扩展数量

    Returns:
        List[str]: 扩展后的所有术语列表
    """
    all_expanded = []

    for term in query_terms[:5]:  # 限制处理数量，避免过度扩展
        try:
            expanded = await intelligent_concept_expansion(
                term,
                max_terms=max_per_term,
                include_original=True
            )
            all_expanded.extend(expanded)
        except Exception as e:
            logger.warning(f"[IntelligentExpansion] 扩展词汇'{term}'失败: {e}")
            all_expanded.append(term)  # 至少保留原词

    # 最终去重
    unique_expanded = []
    seen = set()
    for term in all_expanded:
        if term and term.lower() not in seen:
            seen.add(term.lower())
            unique_expanded.append(term)

    return unique_expanded[:25]  # 总体限制25个术语
