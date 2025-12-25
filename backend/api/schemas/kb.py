# backend/api/schemas/kb.py
"""
知识库相关数据模型
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class KnowledgeBaseCategory(BaseModel):
    """知识库分类"""
    id: str = Field(description="分类ID")
    name: str = Field(description="分类名称")
    description: Optional[str] = Field(default=None, description="分类描述")
    icon: Optional[str] = Field(default=None, description="图标名称（Lucide Icons）")
    item_count: Optional[int] = Field(default=None, description="条目数量")
    tags: Optional[List[str]] = Field(default_factory=list, description="标签列表")


class KnowledgeBaseItem(BaseModel):
    """知识库条目"""
    id: str = Field(description="条目ID")
    title: str = Field(description="标题")
    category: str = Field(description="所属分类")
    source: Optional[str] = Field(default=None, description="来源")
    description: Optional[str] = Field(default=None, description="摘要描述")
    tags: Optional[List[str]] = Field(default_factory=list, description="标签")
    chunk_count: Optional[int] = Field(default=None, description="文档块数量")
    page_count: Optional[int] = Field(default=None, description="页数")
    created_at: Optional[float] = Field(default=None, description="创建时间戳")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="元数据")


class KnowledgeBaseCategoriesResponse(BaseModel):
    """知识库分类列表响应"""
    categories: List[KnowledgeBaseCategory] = Field(description="分类列表")
    total: int = Field(description="总数")


class KnowledgeBaseItemsResponse(BaseModel):
    """知识库条目列表响应"""
    items: List[KnowledgeBaseItem] = Field(description="条目列表")
    category: str = Field(description="分类")
    total: int = Field(description="总数")
    page: int = Field(description="当前页码")
    page_size: int = Field(description="每页数量")
    total_pages: int = Field(description="总页数")


class KnowledgeBaseSearchRequest(BaseModel):
    """知识库搜索请求"""
    query: str = Field(min_length=1, max_length=500, description="搜索关键词")
    category: Optional[str] = Field(default=None, description="指定分类")
    tags: Optional[List[str]] = Field(default_factory=list, description="标签筛选")
    top_k: int = Field(default=10, ge=1, le=100, description="返回结果数量")


class KnowledgeBaseSearchResponse(BaseModel):
    """知识库搜索响应"""
    items: List[KnowledgeBaseItem] = Field(description="搜索结果")
    query: str = Field(description="搜索关键词")
    total: int = Field(description="结果总数")
    took_ms: Optional[int] = Field(default=None, description="搜索耗时（毫秒）")