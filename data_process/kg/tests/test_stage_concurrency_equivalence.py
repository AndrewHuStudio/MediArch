# data_process/kg/tests/test_stage_concurrency_equivalence.py
# -*- coding: utf-8 -*-
"""验证 stage1/stage2 并发改造后, 聚合结果与串行等价。

用 mock LLM(确定性返回) + 内存假缓存, 不连任何外部服务。
对比: max_workers=1 (等价串行) vs max_workers=8 的聚合输出必须一致。
"""

import types

import pytest

from data_process.kg import kg_module
from data_process.kg.kg_module import KgModule, EAPair


@pytest.fixture
def module(monkeypatch):
    # 绕开 __init__ 里的 Neo4j/Schema 依赖, 手工装最小可跑实例
    m = KgModule.__new__(KgModule)
    m.alias_map = {}
    m._active_build_signature = "test_sig"
    m._runtime_cache_collection = None  # 缓存禁用 -> 强制走 LLM 路径
    m.EA_MAX_ROUNDS = 1
    m.EA_NEW_THRESHOLD = 999  # 一轮即停
    m.REL_MAX_ROUNDS = 1
    m.REL_NEW_THRESHOLD = 999
    m.REL_CONTENT_MAX_CHARS = 4000

    # 确定性 mock LLM: 按 chunk 内容返回不同实体/关系
    class FakeLLM:
        def chat_json(self, messages, temperature=0.1):
            prompt = messages[-1]["content"]
            # 实体识别
            if "实体" in prompt or "entities" in prompt.lower():
                if "选址" in prompt:
                    return {"entities": {"选址": {"type": "DesignMethod", "description": "选址方法"}},
                            "attributes": {}}
                return {"entities": {"通用实体": {"type": "Space", "description": "x"}}, "attributes": {}}
            return {}

    m.llm = FakeLLM()
    m.max_workers = 1

    # 简化 helper 依赖
    monkeypatch.setattr(m, "_get_schema_types", lambda: ["Space", "DesignMethod"])
    monkeypatch.setattr(m, "_get_relation_types", lambda: ["关联"])
    return m


def _make_chunks(n):
    return [
        {"chunk_id": f"c{i}", "doc_id": "d1",
         "content": ("选址相关内容 " if i % 3 == 0 else "其他内容 ") * 5,
         "content_type": "text"}
        for i in range(n)
    ]


def test_stage1_serial_vs_concurrent_equivalent(module):
    chunks = _make_chunks(20)

    module.max_workers = 1
    serial = module.stage1_ea_recognition(chunks)
    serial_entities = sorted(p.entity_name for p in serial.ea_pairs)

    module.max_workers = 8
    concurrent = module.stage1_ea_recognition(chunks)
    concurrent_entities = sorted(p.entity_name for p in concurrent.ea_pairs)

    assert concurrent_entities == serial_entities, "并发与串行的实体集合必须一致"
    assert concurrent.stats["total_entities"] == serial.stats["total_entities"]


def test_stage2_serial_vs_concurrent_equivalent(module):
    # stage2 mock: 按 chunk 内容返回确定性三元组
    class FakeRelLLM:
        def chat_json(self, messages, temperature=0.1):
            prompt = messages[-1]["content"]
            if "选址" in prompt:
                return {"triples": [["选址", "关联", "总平面", 0.9]]}
            return {"triples": [["实体A", "关联", "实体B", 0.8]]}

    module.llm = FakeRelLLM()
    ea = [EAPair(entity_name="选址", entity_type="DesignMethod", description="x", attributes=[])]
    chunks = _make_chunks(20)

    module.max_workers = 1
    serial = module.stage2_relation_extraction(chunks, ea)
    serial_trips = sorted(f"{t.subject}|{t.relation}|{t.object}" for t in serial.triplets)

    module.max_workers = 8
    concurrent = module.stage2_relation_extraction(chunks, ea)
    concurrent_trips = sorted(f"{t.subject}|{t.relation}|{t.object}" for t in concurrent.triplets)

    assert concurrent_trips == serial_trips, "并发与串行的三元组集合必须一致"
    # support_count 合并也应一致(全局聚合在主线程, 不受并发影响)
    s_support = {f"{t.subject}|{t.relation}|{t.object}": t.properties.get("support_count")
                 for t in serial.triplets}
    c_support = {f"{t.subject}|{t.relation}|{t.object}": t.properties.get("support_count")
                 for t in concurrent.triplets}
    assert c_support == s_support, "support_count 聚合必须一致"
