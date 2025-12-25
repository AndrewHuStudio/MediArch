# backend/tests/test_base_agent_core.py

import os
import sys
import time
import threading
import asyncio
import pytest

# 让 "backend.*" 成为可导入包：把【项目根目录】加入 sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.app.agents.base_agent import (
    # 数据模型 / Reducers
    AgentRequest,
    AgentItem,
    add_items_with_dedup,
    merge_diagnostics,
    keep_latest_request,
    # 运行时核心
    LLMManager,
    CircuitBreaker,
    AgentMetrics,
    AgentConfig,
    BaseAgent,
)


# ----------------------------
# Reducers
# ----------------------------

@pytest.mark.parametrize(
    "items1, items2, expected_ids",
    [
        ([AgentItem(entity_id="1", name="A")],
         [AgentItem(entity_id="2", name="B"), AgentItem(entity_id="1", name="A")],
         ["1", "2"]),
        ([], [AgentItem(entity_id="x")], ["x"]),
        ([AgentItem(entity_id="same")], [AgentItem(entity_id="same")], ["same"]),
    ],
)
def test_add_items_with_dedup(items1, items2, expected_ids):
    merged = add_items_with_dedup(items1, items2)
    assert [i.entity_id for i in merged] == expected_ids


def test_merge_diagnostics_right_precedence():
    left = {"a": 1, "b": 2}
    right = {"b": 3, "c": 4}
    merged = merge_diagnostics(left, right)
    assert merged == {"a": 1, "b": 3, "c": 4}  # 右值覆盖左值


def test_keep_latest_request():
    old = AgentRequest(query="old")
    new = AgentRequest(query="new")
    assert keep_latest_request(old, None).query == "old"
    assert keep_latest_request(old, new).query == "new"


# ----------------------------
# LLMManager（单例 & 线程安全）
# ----------------------------

def test_llm_manager_singleton_and_thread_safety():
    mgr = LLMManager()
    created = []
    create_count = 0
    lock = threading.Lock()

    def slow_init():
        nonlocal create_count
        # 模拟慢初始化，暴露并发竞态
        time.sleep(0.05)
        with lock:
            create_count += 1
        return object()

    results = []

    def worker():
        obj = mgr.get_or_create("test", slow_init)
        results.append(obj)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    # 所有线程都拿到同一个实例
    assert len({id(x) for x in results}) == 1
    # init 仅被调用一次（双重检查加锁应保证）
    assert create_count == 1

    # clear 后可以重新创建
    mgr.clear("test")
    obj2 = mgr.get_or_create("test", slow_init)
    assert create_count == 2
    assert id(obj2) != id(results[0])


# ----------------------------
# CircuitBreaker
# ----------------------------

def test_circuit_breaker_open_and_recover():
    br = CircuitBreaker(threshold=2, cooldown_sec=1)
    assert br.is_open() is False

    br.record_failure()
    assert br.is_open() is False

    br.record_failure()
    assert br.is_open() is True  # 达到阈值熔断

    # 冷却后应自动关闭
    time.sleep(1.1)
    assert br.is_open() is False

    # 成功后应重置失败计数
    br.record_success()
    assert br.is_open() is False


# ----------------------------
# AgentMetrics
# ----------------------------

def test_agent_metrics_basic():
    m = AgentMetrics()
    assert m.success_rate == 0.0 and m.error_rate == 1.0

    m.record_success(latency_ms=100)
    assert m.total_requests == 1
    assert m.successes == 1 and m.failures == 0
    assert m.success_rate == 1.0

    m.record_failure(error="boom", latency_ms=300)
    assert m.total_requests == 2
    assert m.failures == 1
    # 成功率 0.5、错误率 0.5
    assert pytest.approx(m.success_rate, 0.001) == 0.5
    assert pytest.approx(m.error_rate, 0.001) == 0.5

    # reset
    m.reset()
    assert (m.total_requests, m.successes, m.failures) == (0, 0, 0)


# ----------------------------
# BaseAgent.invoke：超时路径
# ----------------------------

class _SleepyAgent(BaseAgent):
    async def _run(self, request: AgentRequest):
        # 故意睡眠超过超时
        await asyncio.sleep(0.2)
        # 正常不会走到这里
        return AgentItem(items=[], used_query="", diagnostics={})

@pytest.mark.asyncio
async def test_base_agent_invoke_timeout():
    cfg = AgentConfig(name="sleepy", display_name="Sleepy", agent_type="test", timeout_ms=50)
    agent = _SleepyAgent(cfg)
    await agent.setup()

    resp = await agent.invoke(AgentRequest(query="hi"))
    # 超时后应返回一个空响应，并带有诊断信息
    assert isinstance(resp.diagnostics, dict)
    assert "Timeout" in resp.diagnostics.get("message", "")
