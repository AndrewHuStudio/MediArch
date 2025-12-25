"""
系统健康检查工具（暂时禁用）

注意：此模块依赖已删除的 registry 系统。
将在前端开发完毕后，使用新的健康检查机制重新实现。

新的健康检查机制：
- 使用 supervisor_graph 中的 worker 状态
- 基于 graph 导入而非 registry
- 参考 orchestrator_agent/agent.py 中的健康检查函数：
  * check_worker_status(agent_name: str) -> bool
  * get_all_workers_status() -> Dict[str, bool]
  * format_workers_status() -> str

使用示例：
    from backend.app.agents.orchestrator_agent.agent import format_workers_status
    print(format_workers_status())
    # 输出: "orchestrator_agent✅ neo4j_agent✅ milvus_agent✅ ..."
"""

import os
import asyncio
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# TODO: 在前端开发阶段，使用新的健康检查机制
# from backend.app.agents.orchestrator_agent.agent import (
#     check_worker_status,
#     get_all_workers_status,
#     format_workers_status,
# )

try:
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    OpenAI = None


DEFAULT_MODEL_PROMPT = "System health check ping"
DEFAULT_API_KEY_ENV_CANDIDATES = [
    "ORCHESTRATOR_API_KEY",
    "NEO4J_AGENT_API_KEY",
    "MILVUS_AGENT_API_KEY",
    "MONGODB_AGENT_API_KEY",
    "ONLINE_SEARCH_AGENT_API_KEY",
    "RESULT_SYNTHESIZER_AGENT_API_KEY",
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
]
DEFAULT_BASE_URL_ENV_CANDIDATES = [
    "ORCHESTRATOR_BASE_URL",
    "NEO4J_AGENT_BASE_URL",
    "MILVUS_AGENT_BASE_URL",
    "MONGODB_AGENT_BASE_URL",
    "ONLINE_SEARCH_AGENT_BASE_URL",
    "RESULT_SYNTHESIZER_AGENT_BASE_URL",
    "OPENAI_BASE_URL",
    "DEEPSEEK_BASE_URL",
]
DEFAULT_BASE_URL = "https://api.openai.com/v1"


class HealthStatus(Enum):
    """Simple status bucket for health checks."""

    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass
class ToolHealth:
    """Stores the outcome of probing a tool."""

    name: str
    status: HealthStatus
    response_time: float
    error_message: str = ""
    last_check: float = 0.0


@dataclass
class AgentHealth:
    """Stores aggregated health info for an agent."""

    name: str
    status: HealthStatus
    tools: List[ToolHealth]
    config_loaded: bool
    model_available: bool
    error_message: str = ""


def ping_model(api_key: str, base_url: Optional[str], model: str, prompt: str = DEFAULT_MODEL_PROMPT) -> Tuple[float, str]:
    """Perform a synchronous test call against API 易 / OpenAI compatible endpoint."""

    if OpenAI is None:
        raise RuntimeError("openai package is not installed")
    if not api_key:
        raise ValueError("missing api_key")
    if not model:
        raise ValueError("missing model name")

    client = OpenAI(api_key=api_key, base_url=base_url or DEFAULT_BASE_URL)
    start = time.time()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    latency = time.time() - start
    choice = response.choices[0] if response.choices else None
    reply = choice.message.content if choice and getattr(choice, "message", None) else ""
    return latency, reply


# ============================================================================
# 以下功能暂时禁用，等待前端开发完毕后使用新的健康检查机制重新实现
# ============================================================================

# class SystemHealthChecker:
#     """Orchestrates health checks for all registered agents."""
#     ...

# TODO: 在前端开发阶段重新实现
def run_system_health_check_sync() -> str:
    """运行系统健康检查（同步版本）- 暂时禁用"""
    return "系统健康检查功能暂时禁用，将在前端开发完毕后重新实现。\n\n" \
           "请使用以下方式检查 worker 状态：\n" \
           "from backend.app.agents.orchestrator_agent.agent import format_workers_status\n" \
           "print(format_workers_status())"


async def run_system_health_check() -> str:
    """运行系统健康检查（异步版本）- 暂时禁用"""
    return run_system_health_check_sync()


if __name__ == "__main__":
    async def _main() -> None:
        report = await run_system_health_check()
        print(report)

    asyncio.run(_main())
