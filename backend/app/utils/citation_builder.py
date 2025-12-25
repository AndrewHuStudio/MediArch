# backend/app/utils/citation_builder.py
"""
统一的 Citation 构建工具函数

目的：
1. 确保所有 Agent 构建的 citations 格式一致
2. 自动填充必填字段（source, location, snippet）
3. 提供类型安全的构建方法
4. [FIX 2025-12-16] 自动转换 file_path 为相对路径

使用示例：
    from backend.app.utils.citation_builder import build_citation, build_kg_citation, build_doc_citation

    # 知识图谱引用
    citation = build_kg_citation(
        source="医院建筑设计指南",
        entity_label="Space",
        entity_name="手术室",
        snippet="手术室设计要点...",
    )

    # 文档引用
    citation = build_doc_citation(
        source="GB51039-2014",
        page_number=59,
        chapter="第3章",
        section="寻路系统",
        snippet="标识导向系统应...",
    )
"""

import os
from typing import Any, Dict, List, Optional
from pathlib import Path


def _convert_to_relative_path(absolute_path: str) -> str:
    """
    [FIX 2025-12-16] 将绝对路径转换为相对于 documents 目录的相对路径

    Args:
        absolute_path: 绝对路径

    Returns:
        相对路径（用于前端 API 访问）
    """
    if not absolute_path:
        return ""

    try:
        path = Path(absolute_path)

        # 如果不是绝对路径，直接返回
        if not path.is_absolute():
            return absolute_path

        # 尝试相对于 documents 目录
        project_root = Path(__file__).resolve().parents[3]  # backend/app/utils -> project_root
        documents_dir = project_root / "backend" / "databases" / "documents"

        try:
            relative_path = path.relative_to(documents_dir)
            # 使用正斜杠（前端兼容）
            return str(relative_path).replace('\\', '/')
        except ValueError:
            # 如果不在 documents 目录下，尝试从绝对路径中提取
            # 格式: E:\...\documents\标准规范\xxx.pdf -> 标准规范/xxx.pdf
            parts = path.parts
            if 'documents' in parts:
                doc_index = parts.index('documents')
                relative_parts = parts[doc_index + 1:]
                return '/'.join(relative_parts)

            # 实在无法转换，返回文件名
            return path.name
    except Exception as e:
        # 转换失败，返回原始路径
        return absolute_path


def build_citation(
    source: str,
    location: str = "",
    snippet: str = "",
    **kwargs: Any
) -> Dict[str, Any]:
    """
    构建标准 Citation 字典

    Args:
        source: 来源文档名（必填）
        location: 具体位置（可选，默认空字符串）
        snippet: 相关片段（可选，默认空字符串）
        **kwargs: 其他可选字段（chunk_id, page_number, metadata等）

    Returns:
        符合 Citation Schema 的字典
    """
    citation = {
        "source": source or "未知来源",
        "location": location or "",
        "snippet": snippet or "",
    }

    # 添加其他可选字段
    optional_fields = [
        "chunk_id", "page_number", "section", "metadata",
        "page_range", "chapter", "chapter_title", "sub_section",
        "content_type", "image_url", "file_path", "document_path",
        "pdf_url", "positions", "doc_id", "doc_category", "highlight_text",
        "id",  # 用于前端唯一标识
    ]

    for field in optional_fields:
        if field in kwargs and kwargs[field] is not None:
            citation[field] = kwargs[field]

    return citation


def build_kg_citation(
    source: str,
    entity_label: str = "",
    entity_name: str = "",
    snippet: str = "",
    entity_id: Optional[str] = None,
    search_term: Optional[str] = None,
    **kwargs: Any
) -> Dict[str, Any]:
    """
    构建知识图谱引用（Neo4j Agent 专用）

    Args:
        source: 来源文档名
        entity_label: 实体标签（如 Space, Department）
        entity_name: 实体名称（如 手术室）
        snippet: 相关片段
        entity_id: 实体ID（可选）
        search_term: 搜索词（可选）
        **kwargs: 其他可选字段

    Returns:
        知识图谱引用字典
    """
    # 构建 location 描述
    location = f"知识图谱节点: {entity_label}" if entity_label else "知识图谱"

    # 构建 metadata
    metadata = {
        "type": "KnowledgeGraph",
        "agent": "neo4j_agent",
    }

    if entity_id:
        metadata["entity_id"] = entity_id
    if search_term:
        metadata["search_term"] = search_term

    # 合并用户提供的 metadata
    if "metadata" in kwargs:
        metadata.update(kwargs.pop("metadata", {}))

    return build_citation(
        source=source,
        location=location,
        snippet=snippet or f"{entity_label} - {entity_name}",
        metadata=metadata,
        **kwargs
    )


def build_doc_citation(
    source: str,
    page_number: Optional[int] = None,
    chapter: str = "",
    chapter_title: str = "",
    sub_section: str = "",
    snippet: str = "",
    chunk_id: Optional[str] = None,
    content_type: str = "text",
    **kwargs: Any
) -> Dict[str, Any]:
    """
    构建文档引用（MongoDB Agent 专用）

    Args:
        source: 来源文档名
        page_number: 页码（可选）
        chapter: 章号（如 "第3章"）
        chapter_title: 章标题（如 "门诊部设计"）
        sub_section: 小节（如 "3.1 功能布局"）
        snippet: 相关片段
        chunk_id: 数据块ID（可选）
        content_type: 内容类型（text/image/table）
        **kwargs: 其他可选字段

    Returns:
        文档引用字典
    """
    # 构建 location 描述（格式: 页码|章节|小节）
    location_parts = []
    if page_number:
        location_parts.append(f"{page_number}页")
    if chapter and chapter_title:
        location_parts.append(f"{chapter} {chapter_title}")
    elif chapter_title:
        location_parts.append(chapter_title)
    if sub_section:
        location_parts.append(sub_section)

    location = "|".join(location_parts) if location_parts else "位置未知"

    return build_citation(
        source=source,
        location=location,
        snippet=snippet,
        chunk_id=chunk_id,
        page_number=page_number,
        chapter=chapter,
        chapter_title=chapter_title,
        sub_section=sub_section,
        content_type=content_type,
        **kwargs
    )


def build_spec_citation(
    source: str,
    spec_label: str = "",
    spec_name: str = "",
    snippet: str = "",
    slug: Optional[str] = None,
    **kwargs: Any
) -> Dict[str, Any]:
    """
    构建规范引用（设计规范、标准文档）

    Args:
        source: 来源文档名
        spec_label: 规范标签（如 DesignSpec）
        spec_name: 规范名称
        snippet: 相关片段
        slug: 规范标识符（可选）
        **kwargs: 其他可选字段

    Returns:
        规范引用字典
    """
    location = f"知识图谱: {spec_label}" if spec_label else "设计规范"

    metadata = {
        "type": "DesignSpec",
    }

    if slug:
        metadata["slug"] = slug

    # 合并用户提供的 metadata
    if "metadata" in kwargs:
        metadata.update(kwargs.pop("metadata", {}))

    return build_citation(
        source=source or spec_name,
        location=location,
        snippet=snippet or f"{spec_label} - {spec_name}",
        metadata=metadata,
        **kwargs
    )


def validate_citation(citation: Dict[str, Any]) -> bool:
    """
    验证 citation 是否符合 Schema 要求

    Args:
        citation: 待验证的 citation 字典

    Returns:
        True 如果有效，False 否则
    """
    # 必填字段检查
    required_fields = ["source"]
    for field in required_fields:
        if field not in citation or not citation[field]:
            return False

    # 可选字段类型检查
    if "location" in citation and not isinstance(citation["location"], str):
        return False

    if "snippet" in citation and not isinstance(citation["snippet"], str):
        return False

    return True


def normalize_citations(citations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    规范化 citations 列表，确保所有必填字段存在

    Args:
        citations: 原始 citations 列表

    Returns:
        规范化后的 citations 列表

    [FIX 2025-12-16] 自动转换 file_path 为相对路径
    """
    normalized = []

    for citation in citations:
        if not isinstance(citation, dict):
            continue

        # 确保必填字段存在
        normalized_citation = {
            "source": citation.get("source", "未知来源"),
            "location": citation.get("location", ""),
            "snippet": citation.get("snippet", ""),
        }

        # 复制其他字段
        for key, value in citation.items():
            if key not in normalized_citation:
                normalized_citation[key] = value

        # [FIX 2025-12-16] 自动转换 file_path 为相对路径（用于前端 PDF 预览）
        if "file_path" in normalized_citation and normalized_citation["file_path"]:
            normalized_citation["file_path"] = _convert_to_relative_path(
                normalized_citation["file_path"]
            )

        # 同样处理 document_path 或 pdf_url 字段（如果存在）
        if "document_path" in normalized_citation and normalized_citation["document_path"]:
            normalized_citation["document_path"] = _convert_to_relative_path(
                normalized_citation["document_path"]
            )

        # [FIX 2025-12-16] 兼容旧字段命名（pdfUrl -> pdf_url）
        if "pdf_url" not in normalized_citation and normalized_citation.get("pdfUrl"):
            normalized_citation["pdf_url"] = normalized_citation.get("pdfUrl")

        # [FIX 2025-12-16] 避免前端 getApiUrl 再次拼接 /api/v1 导致重复
        if isinstance(normalized_citation.get("pdf_url"), str) and normalized_citation["pdf_url"].startswith("/api/v1/"):
            normalized_citation["pdf_url"] = normalized_citation["pdf_url"].replace("/api/v1", "", 1)

        normalized.append(normalized_citation)

    return normalized
