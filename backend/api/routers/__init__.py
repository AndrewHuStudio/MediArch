# backend/api/routers/__init__.py
"""
API 路由模块

包含所有 API 端点的实现
"""

from backend.api.routers import chat, health, knowledge_base, documents, knowledge_graph

__all__ = ["chat", "health", "knowledge_base", "documents", "knowledge_graph"]
