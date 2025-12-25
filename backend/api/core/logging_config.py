# backend/api/core/logging_config.py
"""
日志配置模块

统一日志格式，支持控制台和文件输出
GBK 编码环境优化（避免 emoji）
"""

import logging
import os
import sys
from pathlib import Path

from backend.api.core.config import settings


def setup_logging() -> logging.Logger:
    """
    设置日志配置

    Returns:
        配置好的 logger 实例
    """
    # 创建日志目录
    log_dir = Path(settings.LOG_FILE).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    # 创建 logger
    logger = logging.getLogger("mediarch_api")
    logger.setLevel(getattr(logging, settings.LOG_LEVEL))

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    # 控制台 Handler（GBK 环境优化）
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)

    # 文件 Handler
    file_handler = logging.FileHandler(
        settings.LOG_FILE,
        mode="a",
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)

    # 日志格式（避免 emoji，使用 ASCII/中文）
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger
