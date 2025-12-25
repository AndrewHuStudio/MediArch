# backend/app/utils/llm_output_parser.py
"""
通用的 LLM 输出解析工具

目的：
1. 处理各种格式的 LLM 输出（JSON、Markdown、纯文本）
2. 提取结构化数据，即使格式不标准
3. 提供详细的日志，便于调试

支持的格式：
- 标准 JSON: {"key": "value"}
- Markdown 代码块: ```json\n{...}\n```
- 混合格式: **思考**: xxx\n```json\n{...}\n```
- 纯文本: 尝试提取关键信息
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar('T', bound=BaseModel)


def extract_json_from_text(text: str) -> Optional[str]:
    """
    从文本中提取 JSON 字符串

    支持的格式：
    1. 纯 JSON: {"key": "value"}
    2. Markdown 代码块: ```json\n{...}\n```
    3. 混合格式: 文本 + JSON

    Args:
        text: 原始文本

    Returns:
        提取的 JSON 字符串，如果没有找到则返回 None
    """
    if not text:
        return None

    text = text.strip()

    # 1. 尝试提取 Markdown 代码块中的 JSON
    # 格式: ```json\n{...}\n``` 或 ```\n{...}\n```
    code_block_patterns = [
        r'```json\s*\n(.*?)\n```',  # ```json ... ```
        r'```\s*\n(\{.*?\})\s*\n```',  # ``` {...} ```
        r'```\s*\n(\[.*?\])\s*\n```',  # ``` [...] ```
    ]

    for pattern in code_block_patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            json_str = match.group(1).strip()
            logger.debug(f"[LLM Parser] 从 Markdown 代码块提取 JSON: {json_str[:100]}...")
            return json_str

    # 2. 尝试提取大括号或方括号包裹的 JSON
    # 格式: {...} 或 [...]
    json_patterns = [
        r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}',  # 嵌套的 {}
        r'\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\]',  # 嵌套的 []
    ]

    for pattern in json_patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            json_str = match.group(0).strip()
            # 验证是否是有效的 JSON
            try:
                json.loads(json_str)
                logger.debug(f"[LLM Parser] 从文本提取 JSON: {json_str[:100]}...")
                return json_str
            except json.JSONDecodeError:
                continue

    # 3. 如果整个文本看起来像 JSON，直接返回
    if (text.startswith('{') and text.endswith('}')) or \
       (text.startswith('[') and text.endswith(']')):
        logger.debug(f"[LLM Parser] 整个文本是 JSON: {text[:100]}...")
        return text

    logger.warning(f"[LLM Parser] 无法从文本中提取 JSON: {text[:200]}...")
    return None


def parse_llm_output(
    output: Any,
    pydantic_model: Type[T],
    fallback_parser: Optional[callable] = None
) -> Optional[T]:
    """
    解析 LLM 输出为 Pydantic 模型

    Args:
        output: LLM 原始输出（可能是字符串、字典、Pydantic 对象）
        pydantic_model: 目标 Pydantic 模型类
        fallback_parser: 兜底解析函数（可选）

    Returns:
        解析后的 Pydantic 对象，失败返回 None
    """
    # 1. 如果已经是目标类型，直接返回
    if isinstance(output, pydantic_model):
        logger.debug(f"[LLM Parser] 输出已经是 {pydantic_model.__name__} 类型")
        return output

    # 2. 如果是字典，尝试直接构造
    if isinstance(output, dict):
        try:
            result = pydantic_model(**output)
            logger.debug(f"[LLM Parser] 从字典构造 {pydantic_model.__name__} 成功")
            return result
        except ValidationError as e:
            logger.warning(f"[LLM Parser] 从字典构造失败: {e}")

    # 3. 如果是字符串，尝试提取 JSON
    if isinstance(output, str):
        json_str = extract_json_from_text(output)
        if json_str:
            try:
                data = json.loads(json_str)
                result = pydantic_model(**data)
                logger.debug(f"[LLM Parser] 从 JSON 字符串构造 {pydantic_model.__name__} 成功")
                return result
            except (json.JSONDecodeError, ValidationError) as e:
                logger.warning(f"[LLM Parser] 从 JSON 字符串构造失败: {e}")

    # 4. 如果有 content 属性（LangChain 的 AIMessage）
    if hasattr(output, 'content'):
        return parse_llm_output(output.content, pydantic_model, fallback_parser)

    # 5. 使用兜底解析器
    if fallback_parser:
        try:
            result = fallback_parser(output)
            if result:
                logger.info(f"[LLM Parser] 使用兜底解析器成功")
                return result
        except Exception as e:
            logger.warning(f"[LLM Parser] 兜底解析器失败: {e}")

    # 6. 记录原始输出，便于调试
    output_preview = str(output)[:500] if output else "None"
    logger.error(
        f"[LLM Parser] 所有解析方法都失败了\n"
        f"目标类型: {pydantic_model.__name__}\n"
        f"原始输出: {output_preview}"
    )

    return None


def extract_list_from_text(text: str, field_name: str = "items") -> List[str]:
    """
    从文本中提取列表

    支持的格式：
    1. JSON 数组: ["item1", "item2"]
    2. Markdown 列表: - item1\n- item2
    3. 编号列表: 1. item1\n2. item2
    4. 逗号分隔: item1, item2, item3

    Args:
        text: 原始文本
        field_name: 字段名（用于日志）

    Returns:
        提取的列表
    """
    if not text:
        return []

    text = text.strip()

    # 1. 尝试提取 JSON 数组
    json_str = extract_json_from_text(text)
    if json_str:
        try:
            data = json.loads(json_str)
            if isinstance(data, list):
                logger.debug(f"[LLM Parser] 从 JSON 提取列表: {data}")
                return [str(item) for item in data]
            elif isinstance(data, dict) and field_name in data:
                items = data[field_name]
                if isinstance(items, list):
                    logger.debug(f"[LLM Parser] 从 JSON 字典提取列表: {items}")
                    return [str(item) for item in items]
        except json.JSONDecodeError:
            pass

    # 2. 尝试提取 Markdown 列表
    # 格式: - item1\n- item2 或 * item1\n* item2
    markdown_pattern = r'^[\-\*]\s+(.+)$'
    markdown_items = re.findall(markdown_pattern, text, re.MULTILINE)
    if markdown_items:
        logger.debug(f"[LLM Parser] 从 Markdown 列表提取: {markdown_items}")
        return [item.strip() for item in markdown_items]

    # 3. 尝试提取编号列表
    # 格式: 1. item1\n2. item2
    numbered_pattern = r'^\d+\.\s+(.+)$'
    numbered_items = re.findall(numbered_pattern, text, re.MULTILINE)
    if numbered_items:
        logger.debug(f"[LLM Parser] 从编号列表提取: {numbered_items}")
        return [item.strip() for item in numbered_items]

    # 4. 尝试逗号分隔
    if ',' in text:
        items = [item.strip() for item in text.split(',') if item.strip()]
        if items:
            logger.debug(f"[LLM Parser] 从逗号分隔提取: {items}")
            return items

    # 5. 如果都失败，返回整个文本作为单个元素
    logger.warning(f"[LLM Parser] 无法提取列表，返回整个文本: {text[:100]}...")
    return [text]


def clean_json_string(json_str: str) -> str:
    """
    清理 JSON 字符串，移除常见的格式问题

    Args:
        json_str: 原始 JSON 字符串

    Returns:
        清理后的 JSON 字符串
    """
    if not json_str:
        return json_str

    # 1. 移除 BOM 标记
    json_str = json_str.lstrip('\ufeff')

    # 2. 移除前后的空白字符
    json_str = json_str.strip()

    # 3. 移除 Markdown 格式标记
    json_str = re.sub(r'\*\*([^*]+)\*\*', r'\1', json_str)  # **text** -> text

    # 4. 修复常见的 JSON 错误
    # 单引号 -> 双引号（但要小心字符串内的单引号）
    # json_str = json_str.replace("'", '"')  # 这个太危险，暂时不用

    return json_str


# ============================================================================
# 便捷函数
# ============================================================================

def safe_parse_json(text: str) -> Optional[Dict[str, Any]]:
    """
    安全地解析 JSON 字符串

    Args:
        text: JSON 字符串

    Returns:
        解析后的字典，失败返回 None
    """
    if not text:
        return None

    json_str = extract_json_from_text(text)
    if not json_str:
        return None

    try:
        json_str = clean_json_string(json_str)
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.warning(f"[LLM Parser] JSON 解析失败: {e}, text={text[:200]}...")
        return None
