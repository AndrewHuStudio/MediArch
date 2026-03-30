# backend/api/main.py
"""
MediArch FastAPI 主应用入口

核心特性:
- CORS 跨域支持（Next.js 前端）
- 流式响应（Server-Sent Events）
- 请求日志中间件
- 性能监控中间件
- 错误处理中间件
- API 版本管理
- 自动生成 OpenAPI 文档
"""

import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import Any, Dict

# 确保项目根目录在 Python 路径中
_current_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_backend_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.openapi.docs import get_swagger_ui_html
from starlette.exceptions import HTTPException as StarletteHTTPException

# 导入路由
from backend.api.routers import chat, health, knowledge_base, documents, knowledge_graph
from data_process.api import router as data_process_router

# 导入核心组件
from backend.api.core.config import settings
from backend.api.core.logging_config import setup_logging
from backend.app.agents.postgres_deployment_policy import (
    validate_required_postgres_persistence,
)

# 设置日志
logger = setup_logging()


def _validate_required_persistence_backends() -> None:
    from backend.app.agents.mediarch_graph import (
        CHECKPOINTER_RUNTIME_STATUS,
        STORE_RUNTIME_STATUS,
    )

    validate_required_postgres_persistence(
        require_postgres=settings.REQUIRE_POSTGRES_PERSISTENCE,
        component_statuses={
            "checkpointer": CHECKPOINTER_RUNTIME_STATUS,
            "store": STORE_RUNTIME_STATUS,
            "api_sessions": chat.SESSION_REPOSITORY_RUNTIME_STATUS,
        },
    )

# ============================================================================
# 生命周期管理
# ============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理

    启动时:
    - 初始化数据库连接
    - 预热 LangGraph MediArch Graph
    - 加载配置

    关闭时:
    - 关闭数据库连接
    - 清理资源
    """
    logger.info("[OK] MediArch FastAPI 正在启动...")
    logger.info(f"[OK] 运行环境: {settings.ENVIRONMENT}")
    logger.info(f"[OK] CORS允许来源: {settings.CORS_ORIGINS}")

    # 启动时初始化
    try:
        _validate_required_persistence_backends()

        # 预热 LangGraph MediArch Graph（可选，提升首次请求速度）
        if settings.PRELOAD_SUPERVISOR:
            from backend.app.agents.mediarch_graph import graph as mediarch_graph
            from backend.app.agents.mediarch_graph import SQLITE_CHECKPOINT_PATH
            from backend.app.agents.persistence import SQLiteCheckpointSaver
            # 初始化异步 Postgres checkpointer（open pool + create tables）
            _ckpt = getattr(mediarch_graph, 'checkpointer', None)
            if _ckpt is not None and hasattr(_ckpt, '_pool'):
                try:
                    await _ckpt._pool.open()
                    await _ckpt.setup()
                    logger.info("[OK] AsyncPostgresSaver pool opened & tables created")
                except Exception as ckpt_error:
                    logger.warning(
                        "[WARN] AsyncPostgresSaver init failed, fallback to SQLiteCheckpointSaver: %s",
                        ckpt_error,
                    )
                    mediarch_graph.checkpointer = SQLiteCheckpointSaver(SQLITE_CHECKPOINT_PATH)
            logger.info("[OK] LangGraph MediArch Graph 预热成功")

        # 初始化数据库连接池（如需要）
        # await init_database_pools()

        logger.info("[OK] MediArch FastAPI 启动完成！")

    except Exception as e:
        logger.error(f"[FAIL] 启动失败: {e}")
        raise

    yield  # 应用运行

    # 关闭时清理
    logger.info("[OK] MediArch FastAPI 正在关闭...")
    # await close_database_pools()


# ============================================================================
# FastAPI 应用实例
# ============================================================================

app = FastAPI(
    title="MediArch API",
    description="综合医院设计问答助手 - 智能检索与咨询 API",
    version="1.0.0",
    docs_url=None,  # 禁用默认文档，使用自定义路由
    redoc_url="/api/redoc" if settings.ENVIRONMENT != "production" else None,
    openapi_url="/api/openapi.json" if settings.ENVIRONMENT != "production" else None,
    lifespan=lifespan,
)

# ============================================================================
# 中间件配置（按执行顺序）
# ============================================================================

# 1. CORS 中间件（必须最先添加）
# 开发环境放宽为通配符，避免预检 400
cors_origins = settings.CORS_ORIGINS
if settings.ENVIRONMENT != "production":
    cors_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=".*" if settings.ENVIRONMENT != "production" else None,
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有 HTTP 方法
    allow_headers=["*"],  # 允许所有 Headers
    expose_headers=["X-Process-Time", "X-Request-ID"],  # 暴露自定义响应头
)


# 2. 请求日志中间件
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """记录所有 HTTP 请求"""
    request_id = request.headers.get("X-Request-ID", f"req-{int(time.time() * 1000)}")
    start_time = time.time()

    # 记录请求信息
    logger.info(
        f"[Request] {request.method} {request.url.path} | "
        f"Client: {request.client.host if request.client else 'unknown'} | "
        f"Request-ID: {request_id}"
    )

    response = await call_next(request)

    # 计算处理时间
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = f"{process_time:.3f}"
    response.headers["X-Request-ID"] = request_id

    # 记录响应信息
    logger.info(
        f"[Response] {request.method} {request.url.path} | "
        f"Status: {response.status_code} | "
        f"Time: {process_time:.3f}s | "
        f"Request-ID: {request_id}"
    )

    return response


# 3. 性能监控中间件（可选，用于慢请求告警）
@app.middleware("http")
async def monitor_performance(request: Request, call_next):
    """监控慢请求"""
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time

    # 慢请求告警（超过5秒）
    if process_time > 5.0:
        logger.warning(
            f"[SLOW] {request.method} {request.url.path} | "
            f"Time: {process_time:.3f}s | "
            f"This is a slow request!"
        )

    return response


# ============================================================================
# 全局异常处理器
# ============================================================================


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """处理 HTTP 异常"""
    logger.error(
        f"[HTTP Error] {request.method} {request.url.path} | "
        f"Status: {exc.status_code} | "
        f"Detail: {exc.detail}"
    )

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.status_code,
                "message": exc.detail,
                "path": str(request.url.path),
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """处理请求验证错误"""
    errors = []
    for error in exc.errors():
        errors.append({
            "loc": " -> ".join(str(x) for x in error["loc"]),
            "msg": error["msg"],
            "type": error["type"],
        })

    logger.warning(
        f"[Validation Error] {request.method} {request.url.path} | "
        f"Errors: {errors}"
    )

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": 422,
                "message": "Request validation failed",
                "details": errors,
            }
        },
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """处理未捕获的异常"""
    logger.exception(
        f"[Internal Error] {request.method} {request.url.path} | "
        f"Exception: {type(exc).__name__}: {str(exc)}"
    )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": 500,
                "message": "Internal server error",
                "detail": str(exc) if settings.ENVIRONMENT != "production" else "An unexpected error occurred",
            }
        },
    )


# ============================================================================
# 路由注册（API 版本管理）
# ============================================================================

# API v1 路由
app.include_router(
    chat.router,
    prefix="/api/v1",
    tags=["Chat & Query"]
)

app.include_router(
    knowledge_base.router,
    prefix="/api/v1",
    tags=["Knowledge Base"]
)

app.include_router(
    health.router,
    prefix="/api/v1",
    tags=["Health & Monitoring"]
)

app.include_router(
    documents.router,
    prefix="/api/v1",
    tags=["Documents"]
)

app.include_router(
    knowledge_graph.router,
    prefix="/api/v1",
    tags=["Knowledge Graph"]
)

app.include_router(
    data_process_router,
    tags=["Data Processing"]
)

# ============================================================================
# 根路径（健康检查）
# ============================================================================


@app.get("/", tags=["Root"])
async def root():
    """根路径健康检查"""
    return {
        "service": "MediArch API",
        "version": "1.0.0",
        "status": "running",
        "docs": "/api/docs",
        "redoc": "/api/redoc",
    }


@app.get("/api/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    """自定义 Swagger UI（使用国内可访问的 CDN）"""
    return get_swagger_ui_html(
        openapi_url="/api/openapi.json",
        title=f"{app.title} - Swagger UI",
        swagger_js_url="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css",
        swagger_favicon_url="https://fastapi.tiangolo.com/img/favicon.png",
    )


@app.get("/ping", tags=["Root"])
async def ping():
    """简单的 ping 端点"""
    return {"message": "pong"}


# ============================================================================
# 开发服务器（仅用于本地调试）
# ============================================================================

if __name__ == "__main__":
    from backend.api.__main__ import main

    main()
