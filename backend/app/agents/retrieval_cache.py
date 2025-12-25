# backend/app/agents/retrieval_cache.py
"""
Retrieval Cache - 检索结果缓存模块

核心功能:
1. 缓存 Neo4j + Milvus 的并行检索结果
2. 缓存 Knowledge Fusion 的融合结果
3. 缓存最终的 graph_data 和 citations
4. 支持 TTL 过期和 LRU 淘汰

2025-11-25 创建
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from collections import OrderedDict
import threading

logger = logging.getLogger("retrieval_cache")


@dataclass
class CacheEntry:
    """缓存条目"""
    key: str
    value: Any
    created_at: float = field(default_factory=time.time)
    ttl_seconds: int = 300  # 默认5分钟
    hit_count: int = 0

    def is_expired(self) -> bool:
        """检查是否过期"""
        return time.time() - self.created_at > self.ttl_seconds


class RetrievalCache:
    """
    检索结果缓存

    特性:
    - LRU 淘汰策略
    - TTL 过期
    - 线程安全
    - 基于 query hash 的 key

    使用示例:
    ```python
    cache = get_retrieval_cache()

    # 检查缓存
    cached = cache.get(query, filters)
    if cached:
        return cached

    # 执行检索...
    result = await do_retrieval(query)

    # 保存缓存
    cache.set(query, filters, result)
    ```
    """

    def __init__(
        self,
        max_size: int = 100,
        default_ttl: int = 300,
        enabled: bool = True,
    ):
        """
        初始化缓存

        Args:
            max_size: 最大缓存条目数
            default_ttl: 默认 TTL（秒）
            enabled: 是否启用缓存
        """
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._enabled = enabled

        # 统计
        self._hits = 0
        self._misses = 0

        logger.info(
            f"[RetrievalCache] 初始化: max_size={max_size}, "
            f"ttl={default_ttl}s, enabled={enabled}"
        )

    def _make_key(self, query: str, filters: Optional[Dict[str, Any]] = None) -> str:
        """生成缓存 key"""
        # 将 query 和 filters 序列化为字符串
        key_parts = [query.strip().lower()]

        if filters:
            # 排序后序列化，确保相同 filters 生成相同 key
            sorted_filters = sorted(filters.items())
            key_parts.append(str(sorted_filters))

        key_str = "|".join(key_parts)

        # 使用 MD5 生成短 key
        return hashlib.md5(key_str.encode()).hexdigest()[:16]

    def get(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        cache_type: str = "default",
    ) -> Optional[Any]:
        """
        获取缓存

        Args:
            query: 查询文本
            filters: 过滤条件
            cache_type: 缓存类型（用于区分不同阶段的缓存）

        Returns:
            缓存的值，如果未命中或已过期则返回 None
        """
        if not self._enabled:
            return None

        key = f"{cache_type}:{self._make_key(query, filters)}"

        with self._lock:
            entry = self._cache.get(key)

            if entry is None:
                self._misses += 1
                return None

            if entry.is_expired():
                # 过期，删除并返回 None
                del self._cache[key]
                self._misses += 1
                logger.debug(f"[RetrievalCache] 缓存过期: {key}")
                return None

            # 命中，更新 LRU 顺序
            self._cache.move_to_end(key)
            entry.hit_count += 1
            self._hits += 1

            logger.info(
                f"[RetrievalCache] 缓存命中: type={cache_type}, "
                f"hits={entry.hit_count}"
            )
            return entry.value

    def set(
        self,
        query: str,
        filters: Optional[Dict[str, Any]],
        value: Any,
        cache_type: str = "default",
        ttl: Optional[int] = None,
    ) -> None:
        """
        设置缓存

        Args:
            query: 查询文本
            filters: 过滤条件
            value: 要缓存的值
            cache_type: 缓存类型
            ttl: 自定义 TTL（秒），None 表示使用默认值
        """
        if not self._enabled:
            return

        key = f"{cache_type}:{self._make_key(query, filters)}"
        ttl = ttl if ttl is not None else self._default_ttl

        with self._lock:
            # 如果已存在，先删除（LRU 更新）
            if key in self._cache:
                del self._cache[key]

            # 检查容量，必要时淘汰
            while len(self._cache) >= self._max_size:
                # 淘汰最旧的（OrderedDict 的第一个）
                oldest_key, _ = self._cache.popitem(last=False)
                logger.debug(f"[RetrievalCache] LRU 淘汰: {oldest_key}")

            # 添加新条目
            entry = CacheEntry(
                key=key,
                value=value,
                created_at=time.time(),
                ttl_seconds=ttl,
            )
            self._cache[key] = entry

            logger.debug(
                f"[RetrievalCache] 缓存设置: type={cache_type}, "
                f"ttl={ttl}s, size={len(self._cache)}"
            )

    def invalidate(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        cache_type: Optional[str] = None,
    ) -> bool:
        """
        使缓存失效

        Args:
            query: 查询文本
            filters: 过滤条件
            cache_type: 缓存类型，None 表示所有类型

        Returns:
            是否成功删除
        """
        if not self._enabled:
            return False

        base_key = self._make_key(query, filters)

        with self._lock:
            if cache_type:
                # 删除指定类型
                key = f"{cache_type}:{base_key}"
                if key in self._cache:
                    del self._cache[key]
                    logger.info(f"[RetrievalCache] 缓存失效: {key}")
                    return True
            else:
                # 删除所有类型
                keys_to_delete = [
                    k for k in self._cache.keys()
                    if k.endswith(f":{base_key}")
                ]
                for k in keys_to_delete:
                    del self._cache[k]
                if keys_to_delete:
                    logger.info(
                        f"[RetrievalCache] 批量失效: {len(keys_to_delete)} 条"
                    )
                    return True

        return False

    def clear(self) -> None:
        """清空缓存"""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._hits = 0
            self._misses = 0
            logger.info(f"[RetrievalCache] 缓存已清空: {count} 条")

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = self._hits / total if total > 0 else 0.0

            return {
                "enabled": self._enabled,
                "size": len(self._cache),
                "max_size": self._max_size,
                "default_ttl": self._default_ttl,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(hit_rate, 3),
            }

    def cleanup_expired(self) -> int:
        """清理过期条目"""
        with self._lock:
            expired_keys = [
                k for k, v in self._cache.items()
                if v.is_expired()
            ]
            for k in expired_keys:
                del self._cache[k]

            if expired_keys:
                logger.info(
                    f"[RetrievalCache] 清理过期条目: {len(expired_keys)} 条"
                )
            return len(expired_keys)


# ============================================================================
# 全局缓存实例
# ============================================================================

_retrieval_cache: Optional[RetrievalCache] = None


def get_retrieval_cache() -> RetrievalCache:
    """获取全局缓存实例"""
    global _retrieval_cache
    if _retrieval_cache is None:
        import os
        enabled = os.getenv("RETRIEVAL_CACHE_ENABLED", "true").lower() == "true"
        max_size = int(os.getenv("RETRIEVAL_CACHE_MAX_SIZE", "100"))
        default_ttl = int(os.getenv("RETRIEVAL_CACHE_TTL", "300"))

        _retrieval_cache = RetrievalCache(
            max_size=max_size,
            default_ttl=default_ttl,
            enabled=enabled,
        )
    return _retrieval_cache


def reset_retrieval_cache() -> None:
    """重置全局缓存（主要用于测试）"""
    global _retrieval_cache
    if _retrieval_cache:
        _retrieval_cache.clear()
    _retrieval_cache = None


# ============================================================================
# 缓存装饰器
# ============================================================================

def cached_retrieval(
    cache_type: str = "default",
    ttl: Optional[int] = None,
):
    """
    检索缓存装饰器

    使用示例:
    ```python
    @cached_retrieval(cache_type="neo4j", ttl=600)
    async def retrieve_from_neo4j(query: str, filters: dict) -> List[AgentItem]:
        # 实际检索逻辑
        pass
    ```
    """
    def decorator(func):
        import functools
        import asyncio

        @functools.wraps(func)
        async def async_wrapper(query: str, filters: Optional[Dict] = None, **kwargs):
            cache = get_retrieval_cache()

            # 检查缓存
            cached = cache.get(query, filters, cache_type)
            if cached is not None:
                return cached

            # 执行原函数
            result = await func(query, filters, **kwargs)

            # 保存缓存
            cache.set(query, filters, result, cache_type, ttl)

            return result

        @functools.wraps(func)
        def sync_wrapper(query: str, filters: Optional[Dict] = None, **kwargs):
            cache = get_retrieval_cache()

            # 检查缓存
            cached = cache.get(query, filters, cache_type)
            if cached is not None:
                return cached

            # 执行原函数
            result = func(query, filters, **kwargs)

            # 保存缓存
            cache.set(query, filters, result, cache_type, ttl)

            return result

        # 根据原函数类型返回相应的包装器
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
