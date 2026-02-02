# backend/app/agents/base_agent.py
"""
Agent 基础设施 - LangChain 1.0 统一架构

核心功能：
1. 标准数据模型（AgentRequest, AgentResponse, AgentItem）
2. LangGraph StateGraph 集成（Reducer, State Types）
3. 线程安全的 LLM 管理器
4. 向后兼容的 BaseAgent（已弃用）
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from operator import add
from typing import Any, Dict, List, Optional, Callable
from typing_extensions import Annotated, TypedDict

from pydantic import BaseModel, Field, field_validator


# ============================================================================
# 数据模型
# ============================================================================

class AgentRequest(BaseModel):
    """标准化的 Agent 请求"""
    query: str = ""
    filters: Dict[str, Any] = Field(default_factory=dict)
    top_k: int = Field(default=20, ge=1, le=100)  # [FIX 2026-01-14] 从8增加到20
    lang: str = Field(default="zh")
    timeout_ms: int = Field(default=1500, ge=100, le=120000)
    trace_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    context: List[str] = Field(default_factory=list)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)

    @field_validator("query")
    @classmethod
    def _normalize_query(cls, value: str) -> str:
        return (value or "").strip()


class AgentItem(BaseModel):
    """单个检索结果"""
    entity_id: Optional[str] = None
    label: Optional[str] = None
    name: Optional[str] = None
    score: Optional[float] = None
    coverage: Optional[float] = None
    attrs: Dict[str, Any] = Field(default_factory=dict)
    edges: List[Dict[str, Any]] = Field(default_factory=list)
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    source: Optional[str] = None
    snippet: Optional[str] = None


class AgentResponse(BaseModel):
    """标准化的 Agent 响应"""
    items: List[AgentItem] = Field(default_factory=list)
    used_query: str = ""
    diagnostics: Dict[str, Any] = Field(default_factory=dict)
    trace_id: Optional[str] = None
    took_ms: Optional[int] = None
    error: Optional[str] = None

    @classmethod
    def empty(cls, *, trace_id: Optional[str] = None, message: str | None = None) -> "AgentResponse":
        """创建空响应"""
        resp = cls(trace_id=trace_id)
        if message:
            resp.diagnostics["message"] = message
        return resp


# ============================================================================
# Reducer 函数
# ============================================================================

def keep_latest_request(existing: AgentRequest | None, new: AgentRequest | None) -> AgentRequest | None:
    """保留最新的请求"""
    return new if new is not None else existing


def add_items_with_dedup(existing: List[AgentItem] | None, new: List[AgentItem] | None) -> List[AgentItem]:
    """
    合并并去重 items（基于 entity_id）

    用于 MediArch Graph 收集所有 Worker 的结果
    """
    existing = existing or []
    new = new or []

    if not existing and not new:
        return []

    def _is_truthy(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, dict, set, tuple)):
            return len(value) > 0
        return True

    def _prefer_as_base(a: AgentItem, b: AgentItem) -> bool:
        """
        当 entity_id 冲突时，决定优先保留哪个 item 作为“主体”。

        规则（从高到低）：
        - 优先 MongoDB（通常含 image_url/pdf_url/positions 等更完整字段）
        - 其次保留原有顺序（a 作为 base）
        """
        if (a.source == "mongodb_agent") != (b.source == "mongodb_agent"):
            return a.source == "mongodb_agent"
        return True

    def _merge_items(a: AgentItem, b: AgentItem) -> AgentItem:
        base, other = (a, b) if _prefer_as_base(a, b) else (b, a)

        # score：优先保留 Milvus 的相似度（若存在），否则取较大值
        score = base.score
        if (base.source != "milvus_agent") and (other.source == "milvus_agent") and other.score is not None:
            score = other.score
        elif score is None and other.score is not None:
            score = other.score
        elif score is not None and other.score is not None:
            score = max(float(score), float(other.score))

        coverage = base.coverage
        if coverage is None and other.coverage is not None:
            coverage = other.coverage
        elif coverage is not None and other.coverage is not None:
            coverage = max(float(coverage), float(other.coverage))

        merged_attrs: Dict[str, Any] = dict(other.attrs or {})
        for k, v in (base.attrs or {}).items():
            if _is_truthy(v) or k not in merged_attrs:
                merged_attrs[k] = v

        # 合并 citations（去重）
        citations: List[Dict[str, Any]] = []
        seen_cite = set()
        for cite in list(base.citations or []) + list(other.citations or []):
            if not isinstance(cite, dict):
                continue
            key = (
                cite.get("source"),
                cite.get("chunk_id"),
                cite.get("location"),
                cite.get("page_number"),
                cite.get("section"),
                cite.get("image_url"),
            )
            if key in seen_cite:
                continue
            seen_cite.add(key)
            citations.append(cite)

        # 合并 edges（去重）
        edges: List[Dict[str, Any]] = []
        seen_edge = set()
        for edge in list(base.edges or []) + list(other.edges or []):
            if not isinstance(edge, dict):
                continue
            key = (edge.get("type"), edge.get("target"))
            if key in seen_edge:
                continue
            seen_edge.add(key)
            edges.append(edge)

        # snippet：优先选择更“信息密度高”的那条
        snippet = base.snippet if _is_truthy(base.snippet) else other.snippet
        if _is_truthy(base.snippet) and _is_truthy(other.snippet):
            snippet = base.snippet if len(str(base.snippet)) >= len(str(other.snippet)) else other.snippet

        # label/name：尽量取非空
        label = base.label or other.label
        name = base.name or other.name

        return AgentItem(
            entity_id=base.entity_id or other.entity_id,
            label=label,
            name=name,
            score=score,
            coverage=coverage,
            attrs=merged_attrs,
            edges=edges,
            citations=citations,
            source=base.source or other.source,
            snippet=snippet,
        )

    merged_by_id: Dict[Any, AgentItem] = {}
    order: List[Any] = []

    for item in list(existing) + list(new):
        key = item.entity_id if item.entity_id else id(item)
        if key not in merged_by_id:
            merged_by_id[key] = item
            order.append(key)
            continue
        merged_by_id[key] = _merge_items(merged_by_id[key], item)

    return [merged_by_id[k] for k in order]


def merge_diagnostics(existing: Dict[str, Any] | None, new: Dict[str, Any] | None) -> Dict[str, Any]:
    """合并诊断信息（右值优先）"""
    existing = existing or {}
    new = new or {}
    return {**existing, **new}


# ============================================================================
# 标准状态类型
# ============================================================================

# 带 Reducer 的类型注解
RequestAnnotated = Annotated[AgentRequest, keep_latest_request]
ItemsAnnotated = Annotated[List[AgentItem], add_items_with_dedup]
DiagnosticsAnnotated = Annotated[Dict[str, Any], merge_diagnostics]
WorkerResponsesAnnotated = Annotated[List[Dict[str, Any]], add]  # 追加，不去重


class BaseWorkerState(TypedDict, total=False):
    """
    Worker Agent 的标准状态基类

    所有 Worker 都应继承此状态，确保与 MediArch Graph 兼容

    示例：
    class MilvusState(BaseWorkerState):
        search_terms: List[str]  # Worker 特定字段
        retrieval_results: List[Dict[str, Any]]
    """
    # 输入字段（从 MediArch Graph 传递）
    request: RequestAnnotated
    query: str

    # 输出字段（返回给 MediArch Graph）
    items: ItemsAnnotated
    diagnostics: DiagnosticsAnnotated


# ============================================================================
# LLM 管理器
# ============================================================================

class LLMManager:
    """
    Async-safe LLM 单例管理器（LangChain 1.0 兼容）

    作用：
    1. 避免重复初始化 LLM（节省资源）
    2. Async-safe（使用 asyncio.Lock 而非 threading.Lock）
    3. 统一管理所有 Agent 的 LLM
    4. 支持 structured output（.with_structured_output()）

    用法：
    from backend.app.agents.base_agent import get_llm_manager
    from langchain.chat_models import init_chat_model
    from pydantic import BaseModel

    manager = get_llm_manager()
    llm = await manager.aget_or_create(
        name="milvus_rewriter",
        init_func=lambda: init_chat_model(model="gpt-4o-mini", api_key="...")
    )

    # LangChain 1.0: Structured Output
    structured_llm = llm.with_structured_output(MyPydanticModel)
    result = await structured_llm.ainvoke([...])
    """

    def __init__(self):
        self._instances: Dict[str, Any] = {}
        self._lock = asyncio.Lock()

    async def aget_or_create(self, name: str, init_func: Callable[[], Any]) -> Any:
        """获取或创建 LLM 实例（async-safe）"""
        # 快速路径：已存在
        if name in self._instances:
            return self._instances[name]

        # 慢速路径：双重检查锁定
        async with self._lock:
            if name in self._instances:
                return self._instances[name]

            try:
                instance = init_func()
                self._instances[name] = instance
                return instance
            except Exception:
                raise

    def get_or_create(self, name: str, init_func: Callable[[], Any]) -> Any:
        """
        同步版本（向后兼容）

        ⚠️ 注意：在纯 async 环境中应使用 aget_or_create()
        此方法仅用于向后兼容，不使用 lock 保护
        """
        if name in self._instances:
            return self._instances[name]

        try:
            instance = init_func()
            self._instances[name] = instance
            return instance
        except Exception:
            raise

    def get(self, name: str) -> Optional[Any]:
        """获取已存在的 LLM（不创建）"""
        return self._instances.get(name)

    async def aclear(self, name: Optional[str] = None):
        """清除 LLM 实例（async 版本，用于测试）"""
        async with self._lock:
            if name:
                self._instances.pop(name, None)
            else:
                self._instances.clear()

    def clear(self, name: Optional[str] = None):
        """清除 LLM 实例（同步版本，向后兼容）"""
        if name:
            self._instances.pop(name, None)
        else:
            self._instances.clear()


# 全局单例
_llm_manager = LLMManager()


def get_llm_manager() -> LLMManager:
    """获取全局 LLM 管理器"""
    return _llm_manager


# ============================================================================
# Agent 配置和监控
# ============================================================================

class AgentStatus(str, Enum):
    """Agent 运行状态"""
    INITIALIZING = "initializing"
    READY = "ready"
    BUSY = "busy"
    ERROR = "error"
    DISABLED = "disabled"


@dataclass
class AgentMetrics:
    """Agent 性能指标"""
    total_requests: int = 0
    successes: int = 0
    failures: int = 0
    avg_latency_ms: float = 0.0
    last_error: Optional[str] = None
    last_error_time: Optional[float] = None
    last_request_time: Optional[float] = None

    def _update_latency(self, latency_ms: float) -> None:
        """更新平均延迟（指数加权）"""
        alpha = 0.1
        if self.avg_latency_ms == 0:
            self.avg_latency_ms = latency_ms
        else:
            self.avg_latency_ms = alpha * latency_ms + \
                (1 - alpha) * self.avg_latency_ms

    @property
    def success_rate(self) -> float:
        return (self.successes / self.total_requests) if self.total_requests else 0.0

    @property
    def error_rate(self) -> float:
        return 1.0 - self.success_rate

    def record_success(self, latency_ms: float) -> None:
        self.total_requests += 1
        self.successes += 1
        self.last_request_time = time.time()
        self._update_latency(latency_ms)

    def record_failure(self, error: str, latency_ms: float) -> None:
        self.total_requests += 1
        self.failures += 1
        self.last_request_time = time.time()
        self.last_error = error
        self.last_error_time = time.time()
        self._update_latency(latency_ms)

    def reset(self) -> None:
        self.total_requests = 0
        self.successes = 0
        self.failures = 0
        self.avg_latency_ms = 0.0
        self.last_error = None
        self.last_error_time = None
        self.last_request_time = None


class AgentConfig(BaseModel):
    """Agent 配置"""
    name: str
    display_name: str
    agent_type: str

    timeout_ms: int = Field(default=1500, ge=100, le=120_000)
    retry: int = Field(default=1, ge=0, le=5)
    retry_delay_ms: int = Field(default=200, ge=0, le=10_000)
    concurrency_limit: int = Field(default=4, ge=1, le=32)

    circuit_breaker_threshold: int = Field(default=3, ge=1, le=20)
    circuit_breaker_cooldown_sec: int = Field(default=60, ge=5, le=3600)

    enabled: bool = True
    requires_model: bool = False

    model_provider: Optional[str] = None
    model: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None

    system_prompt: str = ""
    tool_descriptions: Dict[str, str] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    api_key_env: Optional[str] = None
    base_url_env: Optional[str] = None
    model_env: Optional[str] = None

    class Config:
        extra = "allow"
        protected_namespaces = ()

    @field_validator("temperature")
    @classmethod
    def _clamp_temperature(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return value
        return max(0.0, min(2.0, value))


class CircuitBreaker:
    """熔断器"""

    def __init__(self, threshold: int, cooldown_sec: int) -> None:
        self.threshold = threshold
        self.cooldown_sec = cooldown_sec
        self.consecutive_failures = 0
        self.open_until: float = 0.0

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.open_until = 0.0

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.threshold:
            self.open_until = time.time() + self.cooldown_sec

    def is_open(self) -> bool:
        if self.open_until == 0.0:
            return False
        if time.time() >= self.open_until:
            self.consecutive_failures = 0
            self.open_until = 0.0
            return False
        return True

    def remaining_cooldown(self) -> float:
        if self.open_until == 0.0:
            return 0.0
        return max(0.0, self.open_until - time.time())


# ============================================================================
# BaseAgent（向后兼容，已弃用）
# ============================================================================

class BaseAgent(ABC):
    """
    ⚠️ 已弃用：新 Agent 应使用 StateGraph 而不是继承此类

    迁移指南：
    1. 定义 State TypedDict（继承 BaseWorkerState）
    2. 定义节点函数（node_xxx）
    3. 使用 StateGraph 构建并导出 graph

    示例：backend/app/agents/online_search_agent/agent.py
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self.status = AgentStatus.INITIALIZING
        self.metrics = AgentMetrics()
        self._breaker = CircuitBreaker(
            config.circuit_breaker_threshold,
            config.circuit_breaker_cooldown_sec
        )
        self._semaphore = asyncio.Semaphore(config.concurrency_limit)

    async def setup(self) -> None:
        """初始化资源"""
        if self.status != AgentStatus.INITIALIZING:
            return

        try:
            self._initialize()
            if asyncio.iscoroutinefunction(getattr(self, '_ainitialize', None)):
                await self._ainitialize()

            self.status = AgentStatus.READY if self.config.enabled else AgentStatus.DISABLED
        except Exception:
            self.status = AgentStatus.ERROR
            raise

    def _initialize(self) -> None:
        """同步初始化钩子（可选）"""
        pass

    @abstractmethod
    async def _run(self, request: AgentRequest) -> AgentResponse:
        """核心逻辑（子类必须实现）"""
        raise NotImplementedError

    async def invoke(self, request: AgentRequest) -> AgentResponse:
        """执行 Agent"""
        if not self.config.enabled:
            return AgentResponse.empty(trace_id=request.trace_id, message="Agent disabled")

        if self._breaker.is_open():
            cooldown = self._breaker.remaining_cooldown()
            return AgentResponse.empty(
                trace_id=request.trace_id,
                message=f"Circuit breaker open, {cooldown:.1f}s remaining"
            )

        start_time = time.time()

        async with self._semaphore:
            try:
                self.status = AgentStatus.BUSY

                # 执行核心逻辑
                response = await asyncio.wait_for(
                    self._run(request),
                    timeout=self.config.timeout_ms / 1000.0
                )

                # 记录成功
                latency_ms = (time.time() - start_time) * 1000
                self.metrics.record_success(latency_ms)
                self._breaker.record_success()
                self.status = AgentStatus.READY

                response.took_ms = int(latency_ms)
                return response

            except asyncio.TimeoutError:
                latency_ms = (time.time() - start_time) * 1000
                error_msg = f"Timeout after {self.config.timeout_ms}ms"
                self.metrics.record_failure(error_msg, latency_ms)
                self._breaker.record_failure()
                self.status = AgentStatus.READY

                return AgentResponse.empty(
                    trace_id=request.trace_id,
                    message=error_msg
                )

            except Exception as e:
                latency_ms = (time.time() - start_time) * 1000
                error_msg = str(e)
                self.metrics.record_failure(error_msg, latency_ms)
                self._breaker.record_failure()
                self.status = AgentStatus.READY

                return AgentResponse.empty(
                    trace_id=request.trace_id,
                    message=f"Error: {error_msg}"
                )

    async def health_check(self) -> Dict[str, Any]:
        """健康检查"""
        return {
            "name": self.config.name,
            "status": self.status.value,
            "enabled": self.config.enabled,
            "metrics": {
                "total_requests": self.metrics.total_requests,
                "success_rate": self.metrics.success_rate,
                "avg_latency_ms": self.metrics.avg_latency_ms,
            },
            "circuit_breaker": {
                "is_open": self._breaker.is_open(),
                "consecutive_failures": self._breaker.consecutive_failures,
                "remaining_cooldown": self._breaker.remaining_cooldown(),
            }
        }


# ============================================================================
# LangChain 1.0: Structured Output 辅助函数
# ============================================================================

def create_structured_llm(llm: Any, pydantic_model: type[BaseModel]) -> Any:
    """
    创建支持结构化输出的 LLM（LangChain 1.0 标准）

    Args:
        llm: 已初始化的 LLM 实例
        pydantic_model: Pydantic 模型类

    Returns:
        支持结构化输出的 LLM 实例

    用法：
    from pydantic import BaseModel, Field

    class QueryAnalysis(BaseModel):
        intent: str = Field(description="用户意图")
        keywords: List[str] = Field(description="关键词列表")

    structured_llm = create_structured_llm(llm, QueryAnalysis)
    result: QueryAnalysis = await structured_llm.ainvoke([...])
    """
    return llm.with_structured_output(pydantic_model)


async def call_structured_llm(
    llm: Any,
    pydantic_model: type[BaseModel],
    messages: List[Any],
) -> BaseModel:
    """
    调用 LLM 并返回结构化输出（LangChain 1.0 标准）

    Args:
        llm: 已初始化的 LLM 实例
        pydantic_model: Pydantic 模型类
        messages: 消息列表 [SystemMessage(...), HumanMessage(...)]

    Returns:
        Pydantic 模型实例

    用法：
    result = await call_structured_llm(
        llm=my_llm,
        pydantic_model=QueryAnalysis,
        messages=[
            SystemMessage(content="你是一个查询分析专家"),
            HumanMessage(content="分析这个查询：手术室设计规范")
        ]
    )
    print(result.intent)  # 直接访问结构化字段
    """
    structured_llm = create_structured_llm(llm, pydantic_model)
    result = await structured_llm.ainvoke(messages)
    return result


# ============================================================================
# 工具函数
# ============================================================================

def create_worker_adapter(worker_name: str, worker_graph: Any) -> Callable:
    """
    创建 Worker Adapter 节点

    作用：
    1. 提取 Worker 需要的输入字段
    2. 调用 Worker 子图
    3. 标准化输出格式（items + worker_responses）
    4. 错误隔离

    用法：
    from backend.app.agents.base_agent import create_worker_adapter

    neo4j_adapter = create_worker_adapter("neo4j_agent", neo4j_graph)
    builder.add_node("neo4j_agent", neo4j_adapter)
    """

    async def adapter_node(state: Dict[str, Any]) -> Dict[str, Any]:
        """Adapter 节点函数"""
        # 提取 Worker 需要的输入
        worker_input = {
            "request": state.get("request"),
            "query": state.get("query"),
        }

        try:
            # 调用 Worker 子图
            result = await worker_graph.ainvoke(worker_input)

            # 提取 items（用于去重合并）
            items = result.get("items") or result.get("merged_items") or []

            # 构建 worker_response（用于 Synthesizer）
            worker_response = {
                "agent_name": worker_name,
                "items": items,
                "diagnostics": result.get("diagnostics", {}),
                "used_query": result.get("used_query") or state.get("query"),
                "took_ms": result.get("took_ms"),
                "item_count": len(items),
            }

            return {
                "items": items,
                "worker_responses": [worker_response],
                "completed_workers": [worker_name],
            }

        except Exception as e:
            # 错误隔离：Worker 失败不影响其他 Worker
            error_response = {
                "agent_name": worker_name,
                "items": [],
                "diagnostics": {"error": str(e), "status": "failed"},
                "item_count": 0,
            }

            return {
                "items": [],
                "worker_responses": [error_response],
                "completed_workers": [worker_name],
            }

    return adapter_node


# ============================================================================
# 模块导出
# ============================================================================

__all__ = [
    # 数据模型
    "AgentRequest",
    "AgentResponse",
    "AgentItem",
    # 状态类型和 Reducer
    "BaseWorkerState",
    "RequestAnnotated",
    "ItemsAnnotated",
    "DiagnosticsAnnotated",
    "WorkerResponsesAnnotated",
    "keep_latest_request",
    "add_items_with_dedup",
    "merge_diagnostics",
    # LLM 管理
    "LLMManager",
    "get_llm_manager",
    # LangChain 1.0: Structured Output
    "create_structured_llm",
    "call_structured_llm",
    # 工具函数
    "create_worker_adapter",
    # 配置和监控
    "AgentConfig",
    "AgentStatus",
    "AgentMetrics",
    "CircuitBreaker",
]
