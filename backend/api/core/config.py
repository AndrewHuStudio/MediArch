# backend/api/core/config.py
"""
配置管理模块

使用 Pydantic Settings 管理环境变量
支持多环境配置（dev, staging, production）
"""

import os
from typing import List, Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用配置"""

    # ====================
    # 应用基础配置
    # ====================
    APP_NAME: str = "MediArch API"
    VERSION: str = "1.0.0"
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DEBUG: bool = True

    # ====================
    # API 服务器配置
    # ====================
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_PREFIX: str = "/api/v1"

    # ====================
    # CORS 配置
    # ====================
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",  # Next.js 开发服务器
        "http://localhost:7860",  # Gradio (已废弃)
        "http://127.0.0.1:3000",
        "http://127.0.0.1:7860",
    ]

    # ====================
    # LangGraph MediArch Graph 配置
    # ====================
    PRELOAD_SUPERVISOR: bool = True  # 启动时预热 MediArch Graph
    SUPERVISOR_TIMEOUT_MS: int = 30000  # MediArch Graph 超时时间（毫秒）

    # ====================
    # 会话管理配置
    # ====================
    SESSION_EXPIRE_HOURS: int = 24  # 会话过期时间（小时）
    MAX_HISTORY_LENGTH: int = 20  # 最大对话历史长度

    # ====================
    # 流式响应配置
    # ====================
    ENABLE_SSE: bool = True  # 启用 Server-Sent Events
    SSE_RETRY_MS: int = 3000  # SSE 重试间隔（毫秒）
    SSE_HEARTBEAT_INTERVAL_S: int = 15  # SSE 心跳间隔（秒）

    # ====================
    # 日志配置
    # ====================
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    LOG_FILE: str = "logs/mediarch_api.log"

    # ====================
    # 数据库配置（可选，用于会话存储）
    # ====================
    # PostgreSQL Checkpoint (来自 mediarch_graph)
    POSTGRES_CHECKPOINT_URI: str = os.getenv(
        "POSTGRES_CHECKPOINT_URI",
        "postgresql://postgres:postgres@localhost:5432/mediarch_checkpoints?sslmode=disable"
    )

    # Redis (可选，用于缓存)
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""

    # ====================
    # 安全配置
    # ====================
    # API Key 认证（可选）
    API_KEY_ENABLED: bool = False
    API_KEY_HEADER: str = "X-API-Key"
    API_KEYS: List[str] = []  # 有效的 API Keys

    # 速率限制（可选）
    RATE_LIMIT_ENABLED: bool = False
    RATE_LIMIT_PER_MINUTE: int = 60  # 每分钟请求数限制

    # ====================
    # 性能配置
    # ====================
    MAX_CONCURRENT_REQUESTS: int = 10  # 最大并发请求数
    REQUEST_TIMEOUT_S: int = 60  # 请求超时时间（秒）

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"  # 允许 .env 中的额外变量

        # 支持从 .env.development, .env.production 等文件加载
        @classmethod
        def customise_sources(cls, init_settings, env_settings, file_secret_settings):
            environment = os.getenv("ENVIRONMENT", "development")
            env_file = f".env.{environment}"

            if os.path.exists(env_file):
                # 优先级: .env.{environment} > .env
                return (
                    init_settings,
                    env_settings,
                    file_secret_settings,
                )
            else:
                return (init_settings, env_settings, file_secret_settings)


# 全局配置实例
settings = Settings()


# ====================
# 环境检测工具函数
# ====================

def is_development() -> bool:
    """是否为开发环境"""
    return settings.ENVIRONMENT == "development"


def is_production() -> bool:
    """是否为生产环境"""
    return settings.ENVIRONMENT == "production"


def is_staging() -> bool:
    """是否为预发布环境"""
    return settings.ENVIRONMENT == "staging"
