# backend/api/routers/health.py
"""
健康检查和系统监控 API 路由

核心功能:
- GET /api/v1/health - 快速健康检查
- GET /api/v1/health/detailed - 详细健康状态
- GET /api/v1/metrics - 系统指标
"""

import logging
import time
from typing import Dict, Any, List

from fastapi import APIRouter, HTTPException, status

from backend.api.schemas.health import (
    QuickHealthResponse,
    SystemHealthResponse,
    AgentStatus,
    DatabaseStatus,
    ComponentStatus,
    MetricsResponse,
)
from backend.api.core.config import settings

logger = logging.getLogger("mediarch_api")

router = APIRouter()

# ============================================================================
# 健康检查辅助函数
# ============================================================================


async def _check_agent_status(agent_name: str, agent_module_path: str) -> AgentStatus:
    """检查单个Agent的状态"""
    try:
        start_time = time.time()

        # 动态导入Agent
        exec(f"from {agent_module_path} import graph")
        graph = eval("graph")

        # 检查图是否编译成功
        latency_ms = (time.time() - start_time) * 1000

        return AgentStatus(
            name=agent_name,
            status="healthy",
            agent_type="worker" if "worker" in agent_name.lower() else "supervisor",
            compilation_status="compiled",
            latency_ms=latency_ms,
            message="Agent运行正常",
            last_check=time.time()
        )

    except Exception as e:
        return AgentStatus(
            name=agent_name,
            status="unhealthy",
            agent_type="worker",
            compilation_status="failed",
            message=f"编译失败: {str(e)[:100]}",
            last_check=time.time()
        )


async def _check_database_status(db_name: str, db_type: str) -> DatabaseStatus:
    """检查数据库连接状态"""
    try:
        start_time = time.time()

        # 根据数据库类型执行检查
        if db_type == "neo4j":
            import os
            from dotenv import load_dotenv
            load_dotenv()
            from backend.app.services.graph_retriever import get_neo4j_driver
            driver = get_neo4j_driver()
            database = os.getenv("NEO4J_DATABASE", "neo4j")
            with driver.session(database=database) as session:
                result = session.run("RETURN 1 AS test")
                result.single()

        elif db_type == "mongodb":
            from backend.databases.mongo_ingest.mongo_connector import MongoDBConnector
            connector = MongoDBConnector()
            # 简单连接测试
            connector.get_collection("test")

        elif db_type == "milvus":
            from pymilvus import connections
            connections.connect(alias="default", host="localhost", port="19530")

        latency_ms = (time.time() - start_time) * 1000

        return DatabaseStatus(
            name=db_name,
            status="healthy",
            latency_ms=latency_ms,
            message=f"{db_type}连接正常",
            last_check=time.time()
        )

    except Exception as e:
        return DatabaseStatus(
            name=db_name,
            status="unhealthy",
            message=f"连接失败: {str(e)[:100]}",
            last_check=time.time()
        )


# ============================================================================
# API 端点
# ============================================================================


@router.get("/health", response_model=QuickHealthResponse, summary="快速健康检查")
async def quick_health():
    """
    快速健康检查（轻量级）

    用于负载均衡器和监控系统的健康探测
    """
    try:
        return QuickHealthResponse(
            status="ok",
            message="MediArch API is running",
            timestamp=time.time()
        )
    except Exception as e:
        logger.error(f"[Health] 快速检查失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="服务不可用"
        )


@router.get("/health/detailed", response_model=SystemHealthResponse, summary="详细健康状态")
async def detailed_health():
    """
    详细健康状态检查（重量级）

    检查所有Agent、数据库和外部服务的状态
    """
    try:
        # 检查所有Agent
        agents_to_check = [
            ("Neo4j Agent", "backend.app.agents.neo4j_agent.agent"),
            ("Milvus Agent", "backend.app.agents.milvus_agent.agent"),
            ("MongoDB Agent", "backend.app.agents.mongodb_agent.agent"),
            ("Orchestrator Agent", "backend.app.agents.orchestrator_agent.agent"),
            ("Online Search Agent", "backend.app.agents.online_search_agent.agent"),
            ("Result Synthesizer Agent", "backend.app.agents.result_synthesizer_agent.agent"),
            ("MediArch Graph", "backend.app.agents.mediarch_graph"),
        ]

        agent_statuses: List[AgentStatus] = []
        for agent_name, module_path in agents_to_check:
            status_obj = await _check_agent_status(agent_name, module_path)
            agent_statuses.append(status_obj)

        # 检查数据库
        databases_to_check = [
            ("Neo4j", "neo4j"),
            ("MongoDB", "mongodb"),
            ("Milvus", "milvus"),
        ]

        db_statuses: List[DatabaseStatus] = []
        for db_name, db_type in databases_to_check:
            status_obj = await _check_database_status(db_name, db_type)
            db_statuses.append(status_obj)

        # 检查外部服务（可选）
        external_statuses: List[ComponentStatus] = []

        # 计算整体状态
        all_healthy = all(
            s.status == "healthy" for s in agent_statuses + db_statuses + external_statuses
        )
        any_unhealthy = any(
            s.status == "unhealthy" for s in agent_statuses + db_statuses + external_statuses
        )

        if all_healthy:
            overall_status = "healthy"
        elif any_unhealthy:
            overall_status = "unhealthy"
        else:
            overall_status = "degraded"

        return SystemHealthResponse(
            overall_status=overall_status,
            timestamp=time.time(),
            agents=agent_statuses,
            databases=db_statuses,
            external_services=external_statuses,
            system_metrics={}
        )

    except Exception as e:
        logger.exception(f"[Health Detailed] 检查失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"健康检查失败: {str(e)}"
        )


@router.get("/metrics", response_model=MetricsResponse, summary="系统指标")
async def get_metrics():
    """
    获取系统性能指标

    包括API请求统计、Agent性能和系统资源使用情况
    """
    try:
        # TODO: 实现真实的指标收集
        # 这里返回模拟数据，生产环境应接入 Prometheus/Grafana

        return MetricsResponse(
            timestamp=time.time(),
            api_metrics={
                "total_requests": 0,
                "requests_per_second": 0.0,
                "average_response_time_ms": 0.0,
                "error_rate": 0.0,
            },
            agent_metrics={
                "neo4j_avg_latency_ms": 0.0,
                "milvus_avg_latency_ms": 0.0,
                "mongodb_avg_latency_ms": 0.0,
                "mediarch_graph_avg_latency_ms": 0.0,
            },
            system_metrics={
                "cpu_usage_percent": 0.0,
                "memory_usage_percent": 0.0,
                "disk_usage_percent": 0.0,
            }
        )

    except Exception as e:
        logger.exception(f"[Metrics] 获取失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取系统指标失败"
        )
