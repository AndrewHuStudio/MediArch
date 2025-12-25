# backend/api/core/__init__.py
"""
核心模块

包含配置、日志、工具函数等基础设施
"""

from backend.api.core.config import settings
from backend.api.core.logging_config import setup_logging

__all__ = ["settings", "setup_logging"]
