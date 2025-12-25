# backend/api/schemas/health.py
"""
健康检查和系统状态相关数据模型
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ComponentStatus(BaseModel):
    """组件状态"""
    name: str = Field(description="组件名称")
    status: str = Field(description="状态: healthy/unhealthy/unknown")
    latency_ms: Optional[float] = Field(default=None, description="响应延迟（毫秒）")
    message: Optional[str] = Field(default=None, description="状态描述")
    details: Optional[Dict[str, Any]] = Field(default_factory=dict, description="详细信息")
    last_check: Optional[float] = Field(default=None, description="最后检查时间戳")


class AgentStatus(ComponentStatus):
    """智能体状态"""
    agent_type: str = Field(description="智能体类型")
    compilation_status: str = Field(description="编译状态")
    last_execution_ms: Optional[float] = Field(default=None, description="最近执行时间（毫秒）")


class DatabaseStatus(ComponentStatus):
    """数据库状态"""
    connection_pool_size: Optional[int] = Field(default=None, description="连接池大小")
    active_connections: Optional[int] = Field(default=None, description="活跃连接数")
    version: Optional[str] = Field(default=None, description="数据库版本")


class SystemHealthResponse(BaseModel):
    """系统健康状态响应"""
    overall_status: str = Field(description="整体状态: healthy/degraded/unhealthy")
    timestamp: float = Field(description="检查时间戳")

    # 各组件状态
    agents: List[AgentStatus] = Field(description="智能体状态列表")
    databases: List[DatabaseStatus] = Field(description="数据库状态列表")
    external_services: List[ComponentStatus] = Field(description="外部服务状态列表")

    # 系统指标
    system_metrics: Optional[Dict[str, Any]] = Field(default_factory=dict, description="系统指标")


class QuickHealthResponse(BaseModel):
    """快速健康检查响应"""
    status: str = Field(description="状态: ok/error")
    message: str = Field(description="状态描述")
    timestamp: float = Field(description="检查时间戳")


class MetricsResponse(BaseModel):
    """系统指标响应"""
    timestamp: float = Field(description="采集时间戳")

    # API 指标
    api_metrics: Dict[str, Any] = Field(description="API 相关指标")

    # 智能体指标
    agent_metrics: Dict[str, Any] = Field(description="智能体性能指标")

    # 系统资源指标
    system_metrics: Dict[str, Any] = Field(description="系统资源使用情况")