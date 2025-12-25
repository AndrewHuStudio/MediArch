# backend/api/routers/knowledge_base.py
"""
知识库 API 路由

核心功能:
- GET /api/v1/kb/categories - 获取知识库分类列表
- GET /api/v1/kb/categories/{category_id}/items - 获取分类下的条目
- POST /api/v1/kb/search - 搜索知识库
"""

import logging
from typing import List, Dict, Any

from fastapi import APIRouter, HTTPException, Query, status

from backend.api.schemas.kb import (
    KnowledgeBaseCategoriesResponse,
    KnowledgeBaseItemsResponse,
    KnowledgeBaseSearchRequest,
    KnowledgeBaseSearchResponse,
    KnowledgeBaseCategory,
    KnowledgeBaseItem,
)
from backend.api.schemas.common import PaginationParams

logger = logging.getLogger("mediarch_api")

router = APIRouter()

# ============================================================================
# 模拟知识库数据（生产环境应从数据库读取）
# ============================================================================

MOCK_CATEGORIES = [
    {
        "id": "regulations",
        "name": "规范标准",
        "description": "国家及行业医疗建筑设计规范",
        "icon": "FileText",
        "item_count": 45,
        "tags": ["规范", "标准", "法规"],
    },
    {
        "id": "books",
        "name": "专业书籍",
        "description": "医院建筑设计专业教材和参考书",
        "icon": "BookOpen",
        "item_count": 128,
        "tags": ["教材", "参考书", "设计手册"],
    },
    {
        "id": "papers",
        "name": "学术论文",
        "description": "医院设计相关学术研究论文",
        "icon": "GraduationCap",
        "item_count": 356,
        "tags": ["论文", "研究", "学术"],
    },
    {
        "id": "cases",
        "name": "设计案例",
        "description": "国内外优秀医院建筑设计案例",
        "icon": "Building",
        "item_count": 89,
        "tags": ["案例", "实例", "项目"],
    },
    {
        "id": "policies",
        "name": "政策文件",
        "description": "医疗卫生相关政策和指导文件",
        "icon": "FileCheck",
        "item_count": 67,
        "tags": ["政策", "文件", "指南"],
    },
]


# ============================================================================
# API 端点
# ============================================================================


@router.get("/kb/categories", response_model=KnowledgeBaseCategoriesResponse, summary="获取知识库分类")
async def get_categories():
    """
    获取所有知识库分类

    Returns:
        分类列表
    """
    try:
        categories = [KnowledgeBaseCategory(**cat) for cat in MOCK_CATEGORIES]

        return KnowledgeBaseCategoriesResponse(
            categories=categories,
            total=len(categories)
        )

    except Exception as e:
        logger.exception(f"[KB Categories] 获取失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取知识库分类失败"
        )


@router.get("/kb/categories/{category_id}/items", response_model=KnowledgeBaseItemsResponse, summary="获取分类条目")
async def get_category_items(
    category_id: str,
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页数量"),
):
    """
    获取指定分类下的知识库条目

    Args:
        category_id: 分类ID
        page: 页码
        page_size: 每页数量

    Returns:
        条目列表（分页）
    """
    try:
        # 验证分类是否存在
        if category_id not in [cat["id"] for cat in MOCK_CATEGORIES]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"分类 {category_id} 不存在"
            )

        # TODO: 从数据库查询真实数据
        # 这里返回模拟数据
        mock_items = []
        category_obj = next(cat for cat in MOCK_CATEGORIES if cat["id"] == category_id)
        total = category_obj["item_count"]

        # 生成分页的模拟数据
        start_idx = (page - 1) * page_size
        end_idx = min(start_idx + page_size, total)

        for i in range(start_idx, end_idx):
            mock_items.append(
                KnowledgeBaseItem(
                    id=f"{category_id}-item-{i}",
                    title=f"示例文档 {i+1}",
                    category=category_id,
                    source="模拟来源",
                    description="这是一条模拟的知识库条目描述...",
                    tags=category_obj["tags"],
                    chunk_count=50 + i,
                    page_count=100 + i,
                    created_at=1700000000.0 + i * 1000,
                )
            )

        total_pages = (total + page_size - 1) // page_size

        return KnowledgeBaseItemsResponse(
            items=mock_items,
            category=category_id,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[KB Items] 获取失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取知识库条目失败"
        )


@router.post("/kb/search", response_model=KnowledgeBaseSearchResponse, summary="搜索知识库")
async def search_knowledge_base(request: KnowledgeBaseSearchRequest):
    """
    搜索知识库

    Args:
        request: 搜索请求

    Returns:
        搜索结果
    """
    try:
        logger.info(f"[KB Search] 搜索关键词: {request.query}")

        # TODO: 集成真实的搜索逻辑（调用 Milvus/MongoDB）
        # 这里返回模拟数据

        mock_results = [
            KnowledgeBaseItem(
                id=f"search-result-{i}",
                title=f"{request.query} - 相关文档 {i+1}",
                category=request.category or "papers",
                source="搜索结果",
                description=f"这是与「{request.query}」相关的模拟搜索结果...",
                tags=request.tags or ["搜索"],
                chunk_count=30,
                page_count=50,
            )
            for i in range(min(request.top_k, 5))
        ]

        return KnowledgeBaseSearchResponse(
            items=mock_results,
            query=request.query,
            total=len(mock_results),
            took_ms=100,
        )

    except Exception as e:
        logger.exception(f"[KB Search] 搜索失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="搜索知识库失败"
        )
