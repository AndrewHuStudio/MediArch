# backend/app/agents/neo4j_agent/llm_parser_patch.py
"""
Neo4j Agent LLM 解析增强补丁

使用方法：
在 agent.py 中导入并替换原有的 analyse_query_with_llm 函数
"""

import logging
from typing import Optional

from langchain_core.messages import SystemMessage, HumanMessage

from backend.app.agents.base_agent import get_llm_manager
from backend.app.utils.llm_output_parser import parse_llm_output

logger = logging.getLogger(__name__)


async def analyse_query_with_llm_enhanced(query: str, QueryAnalysisResult) -> Optional:
    """
    调用 LLM 获取查询意图与关键词（增强版 - 2025-12-09）

    改进：
    - 使用通用的 LLM 输出解析器
    - 处理各种格式的 LLM 输出（JSON、Markdown、纯文本）
    - 添加详细的调试日志
    - 更明确的 system_prompt，要求输出 JSON
    """
    # 获取 LLM
    try:
        manager = get_llm_manager()
        if "neo4j_analysis" in manager._instances:
            llm = manager._instances["neo4j_analysis"]
        else:
            logger.warning(f"[Neo4jAgent] LLM 未初始化，将使用启发式逻辑")
            return None
    except Exception as e:
        logger.warning(f"[Neo4jAgent] 无法获取 LLM: {e}，将使用启发式逻辑")
        return None

    # 更明确的 system_prompt
    system_prompt = (
        "你是一名医院建筑知识图谱的查询分析助手。"
        "请判断用户问题的意图类型，并输出适合图谱检索的关键词。"
        "\n\n**重要：你必须返回有效的 JSON 格式，不要包含任何其他文本。**"
        "\n\n输出格式："
        "\n```json"
        "\n{"
        '\n  "query_type": "entity",  // 必须是: entity, relation, community, mixed 之一'
        '\n  "search_terms": ["手术室", "洁净手术部", "手术间"],  // 关键词列表，至少3个'
        '\n  "reasoning": "用户询问手术室的设计要点，属于实体查询"'
        "\n}"
        "\n```"
        "\n\n示例："
        "\n1. 问题：手术室的设计要点？"
        '\n   输出：{"query_type": "entity", "search_terms": ["手术室", "洁净手术部", "手术间"], "reasoning": "实体查询"}'
        "\n2. 问题：门诊部和住院部的关系？"
        '\n   输出：{"query_type": "relation", "search_terms": ["门诊部", "住院部", "功能分区"], "reasoning": "关系查询"}'
        "\n3. 问题：急诊科有哪些子科室？"
        '\n   输出：{"query_type": "community", "search_terms": ["急诊科", "急诊部"], "reasoning": "社区查询"}'
    )

    try:
        # 调用 LLM
        raw_result = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"用户问题：{query}\n\n请直接返回 JSON，不要包含其他文本。")
        ])

        # 记录原始输出（用于调试）
        if hasattr(raw_result, 'content'):
            logger.debug(f"[Neo4jAgent] LLM 原始输出: {raw_result.content[:500]}...")
        else:
            logger.debug(f"[Neo4jAgent] LLM 原始输出: {str(raw_result)[:500]}...")

        # 使用通用解析器
        result = parse_llm_output(
            output=raw_result,
            pydantic_model=QueryAnalysisResult,
            fallback_parser=None
        )

        if result:
            logger.info(
                f"[Neo4jAgent] LLM 分析成功: "
                f"query_type={result.query_type}, "
                f"terms={result.search_terms[:5] if len(result.search_terms) > 5 else result.search_terms}"
            )
            return result
        else:
            logger.warning(f"[Neo4jAgent] LLM 输出解析失败，将使用启发式逻辑")
            return None

    except Exception as e:
        logger.error(f"[Neo4jAgent] LLM 查询分析异常: {e}，将使用启发式逻辑", exc_info=True)
        return None
