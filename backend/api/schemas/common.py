# backend/api/schemas/common.py
"""
通用数据模型
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict


class APIResponse(BaseModel):
    """API 标准响应格式"""
    success: bool = True
    message: str = ""
    data: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None


class PaginationParams(BaseModel):
    """分页参数"""
    page: int = Field(default=1, ge=1, description="页码（从1开始）")
    page_size: int = Field(default=20, ge=1, le=100, description="每页条数")


class PaginatedResponse(BaseModel):
    """分页响应"""
    items: List[Any]
    total: int
    page: int
    page_size: int
    total_pages: int

    @classmethod
    def create(cls, items: List[Any], total: int, page: int, page_size: int):
        """创建分页响应"""
        total_pages = (total + page_size - 1) // page_size
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages
        )


class Citation(BaseModel):
    """引用信息"""
    model_config = ConfigDict(extra="allow")

    source: str = Field(description="来源文档名")
    location: str = Field(default="", description="具体位置（页码、章节等）")  # [FIX] 改为可选，默认空字符串
    snippet: str = Field(default="", description="相关片段")  # [FIX] 也改为可选，保持一致性
    chunk_id: Optional[str] = Field(default=None, description="数据块ID")
    page_number: Optional[int] = Field(default=None, description="页码")
    section: Optional[str] = Field(default=None, description="章节")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="元数据")
    page_range: Optional[List[int]] = Field(default=None, description="页码范围 [start, end]")
    chapter: Optional[str] = Field(default=None, description="章标题")
    chapter_title: Optional[str] = Field(default=None, description="章名称")
    sub_section: Optional[str] = Field(default=None, description="节名称")
    content_type: Optional[str] = Field(default=None, description="内容类型 text/image/table")
    image_url: Optional[str] = Field(default=None, description="相关图片 URL")
    file_path: Optional[str] = Field(default=None, description="PDF 文件路径")
    document_path: Optional[str] = Field(default=None, description="相对文档路径（用于预览）")
    pdf_url: Optional[str] = Field(default=None, description="文档预览 API 相对地址")
    positions: Optional[List[Dict[str, Any]]] = Field(default=None, description="高亮坐标数组")
    doc_id: Optional[str] = Field(default=None, description="MongoDB 文档 ID")
    doc_category: Optional[str] = Field(default=None, description="文档类型/分类")
    highlight_text: Optional[str] = Field(default=None, description="高亮文本")


class DiagnosticInfo(BaseModel):
    """诊断信息（用于调试和监控）"""
    took_ms: Optional[int] = Field(default=None, description="执行时间（毫秒）")
    agent_name: Optional[str] = Field(default=None, description="代理名称")
    query_type: Optional[str] = Field(default=None, description="查询类型")
    items_count: Optional[int] = Field(default=None, description="结果数量")
    error: Optional[str] = Field(default=None, description="错误信息")
    additional_info: Optional[Dict[str, Any]] = Field(default_factory=dict, description="附加信息")
