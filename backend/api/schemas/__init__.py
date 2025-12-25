# backend/api/schemas/__init__.py
"""
API 数据模型（Pydantic Schemas）

定义请求和响应的数据结构，提供自动验证和文档生成
"""

from backend.api.schemas.chat import *
from backend.api.schemas.health import *
from backend.api.schemas.kb import *
from backend.api.schemas.common import *
