# data_process/kg/concurrency.py
# -*- coding: utf-8 -*-
"""chunk 级并发处理辅助。

KG 构建各阶段对每个 chunk 的处理是相互独立的 I/O 密集任务(等 LLM 返回)。
用线程池并发调用, 结果按输入顺序返回, 主线程负责后续合并 —— 调用方在
process_fn 内部只算"局部结果", 不碰共享状态, 因此线程安全。
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, List, Optional


def map_chunks_concurrent(
    items: List[Any],
    process_fn: Callable[[Any], Any],
    max_workers: int = 8,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> List[Any]:
    """并发地对 items 逐个应用 process_fn, 返回与输入等长、同序的结果列表。

    Args:
        items: 输入项(如 chunk)列表
        process_fn: 处理单个项的纯函数; 内部可调 LLM。抛异常 -> 该位置返回 None
        max_workers: 最大并发线程数
        progress_cb: 可选, fn(done_count, total); 每完成一项调用一次(线程安全)

    Returns:
        结果列表, results[i] 对应 items[i] (异常项为 None)
    """
    total = len(items)
    if total == 0:
        return []

    workers = max(1, min(int(max_workers), total))
    results: List[Any] = [None] * total

    done = 0
    lock = threading.Lock()

    def _run(index: int, item: Any):
        return index, process_fn(item)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_run, i, item) for i, item in enumerate(items)]
        for fut in as_completed(futures):
            try:
                idx, value = fut.result()
                results[idx] = value
            except Exception:  # noqa: BLE001
                # process_fn 抛异常: 该位置保持初始 None (results 已预填 None),
                # 不影响其余 item。done 仍照常递增。
                pass
            finally:
                if progress_cb is not None:
                    with lock:
                        done += 1
                        progress_cb(done, total)

    return results
