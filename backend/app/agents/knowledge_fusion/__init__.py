# backend/app/agents/knowledge_fusion/__init__.py
"""
Knowledge Fusion Module - 知识融合模块

核心功能:
1. 合并 Neo4j 和 Milvus 的并行检索结果
2. 生成统一检索线索 (unified_hints)
3. 构建完整的答案图谱数据 (graph_data)
4. 支持前端知识图谱可视化和 PDF 高亮

2025-11-25 创建
"""

from .fusion import (
    KnowledgeFusionResult,
    fuse_retrieval_results,
    build_unified_hints,
    build_answer_graph_data,
)

__all__ = [
    "KnowledgeFusionResult",
    "fuse_retrieval_results",
    "build_unified_hints", 
    "build_answer_graph_data",
]
