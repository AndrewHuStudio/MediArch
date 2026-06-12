# data_process/kg/tests/test_concurrency.py
# -*- coding: utf-8 -*-
"""并发处理 chunk 的辅助函数测试。

核心正确性: 并发处理一批 chunk, 结果(按输入顺序返回)必须与串行完全一致,
且并发不能丢项、不能乱序。不依赖任何外部服务。
"""

import time

from data_process.kg.concurrency import map_chunks_concurrent


def test_results_match_serial_and_preserve_order():
    items = list(range(50))

    def process(x):
        return x * x

    serial = [process(x) for x in items]
    concurrent = map_chunks_concurrent(items, process, max_workers=8)

    assert concurrent == serial, "并发结果必须与串行一致且保持输入顺序"


def test_concurrency_actually_overlaps():
    """8 个并发跑 8 个各 sleep 0.2s 的任务, 总耗时应远小于串行的 1.6s。"""
    items = list(range(8))

    def slow(x):
        time.sleep(0.2)
        return x

    t0 = time.time()
    out = map_chunks_concurrent(items, slow, max_workers=8)
    elapsed = time.time() - t0

    assert out == items
    assert elapsed < 0.8, f"并发应明显快于串行 1.6s, 实际 {elapsed:.2f}s"


def test_progress_callback_called_once_per_item():
    items = list(range(20))
    seen = []

    def process(x):
        return x

    # progress_cb(done_count, total) — 完成计数, 线程安全累加
    def progress(done, total):
        seen.append((done, total))

    map_chunks_concurrent(items, process, max_workers=4, progress_cb=progress)

    assert len(seen) == 20, "每个 item 完成时应回调一次"
    assert seen[-1][0] == 20, "最后一次完成计数应等于总数"
    assert all(t == 20 for _, t in seen), "total 应始终为 20"
    # 完成计数应单调递增到 20 (不要求严格连续, 但应覆盖 1..20)
    assert {d for d, _ in seen} == set(range(1, 21))


def test_exception_in_one_item_does_not_kill_others():
    """单个 item 抛异常时, 该位置返回 None, 其余正常完成。"""
    items = list(range(10))

    def flaky(x):
        if x == 5:
            raise RuntimeError("boom")
        return x * 10

    out = map_chunks_concurrent(items, flaky, max_workers=4)

    assert len(out) == 10
    assert out[5] is None, "抛异常的 item 应为 None"
    assert out[0] == 0 and out[9] == 90, "其余 item 正常"
