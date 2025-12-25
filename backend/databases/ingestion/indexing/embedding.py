"""
Embedding 生成器（优化版）

改进：
- 复用 requests.Session（HTTPAdapter + Retry）
- 更严谨的批量对齐与重试（支持 Retry-After）
- SQLite WAL / 上下文管理；TTL 过期即清理；可选二进制缓存
- 缓存键包含 model/base_url；维度校验
"""

import os
import time
import json
import hashlib
import sqlite3
import logging
from typing import List, Dict, Optional, Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class EmbeddingGenerator:
    """文本向量生成器"""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_sec: float = 60.0,
    ):
        """
        初始化Embedding生成器
        
        Args:
            api_key: API密钥（默认从环境变量读取）
            base_url: API基础URL（默认从环境变量读取）
            model: 模型名称（默认从环境变量读取）
            timeout_sec: 请求超时时间
        """
        self.api_key = api_key or os.getenv("EMBEDDING_API_KEY")
        self.base_url = (base_url or os.getenv("EMBEDDING_BASE_URL")).rstrip("/")
        self.model = model or os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")
        self.timeout_sec = float(timeout_sec)
        
        if not self.api_key:
            raise ValueError("EMBEDDING_API_KEY未设置")
    
        # 进程内缓存
        self._cache: Dict[str, List[float]] = {}

        # SQLite 持久缓存
        self.cache_db_path = os.getenv("EMBED_CACHE_DB", "backend/databases/ingestion/embed_cache.sqlite")
        self.cache_ttl_sec = int(os.getenv("EMBED_CACHE_TTL", "2592000"))  # 30天
        self.cache_max_entries = int(os.getenv("EMBED_CACHE_MAX", "200000"))
        self.cache_binary = os.getenv("EMBED_CACHE_BINARY", "0") == "1"
        self._init_cache_db()

        # 速率设置
        self.rate_delay = float(os.getenv("EMBED_RATE_DELAY", "0.0"))
        self.max_retry = int(os.getenv("EMBED_RETRY", "3"))

        # HTTP 会话（连接复用 + 自动重试）
        self._session = self._build_session()

        # 预构建请求信息
        self._url = f"{self.base_url}/embeddings"
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    # ---------- 公共方法 ----------
    def generate(self, text: str) -> List[float]:
        """生成单个文本的向量"""
        if not text or not text.strip():
            raise ValueError("文本不能为空")
        
        key = self._ckey(text)
        if key in self._cache:
            return self._cache[key]

        cached = self._cache_get(key)
        if cached is not None:
            self._cache[key] = cached
            return cached

        payload = {"model": self.model, "input": text}
        if self.rate_delay > 0:
            time.sleep(self.rate_delay)

        result = self._post_with_retry(payload)
        try:
            data = result["data"]
            emb = data[0]["embedding"]
        except (KeyError, IndexError) as e:
            raise ValueError(f"Embedding API响应格式错误: {e}")
    
        self._cache[key] = emb
        self._cache_put(key, emb)
        return emb

    def generate_batch(self, texts: List[str], batch_size: int = 100) -> List[List[float]]:
        """
        批量生成向量
        
        - 先查内存与SQLite缓存
        - 对 miss 的输入调用一次接口（保持顺序对齐）
        - 退避重试，支持 Retry-After
        """
        if not texts:
            return []

        embeddings: List[List[float]] = []
        n = len(texts)

        for start in range(0, n, batch_size):
            batch = texts[start : start + batch_size]

            keys = [self._ckey(t) for t in batch]
            batch_cached: Dict[str, List[float]] = {}
            miss_inputs: List[str] = []
            miss_keys: List[str] = []

            # 内存命中
            for t, k in zip(batch, keys):
                v = self._cache.get(k)
                if v is not None:
                    batch_cached[k] = v
                else:
                    miss_inputs.append(t)
                    miss_keys.append(k)

            # SQLite 命中
            if miss_inputs:
                for t, k in list(zip(miss_inputs, miss_keys)):
                    v = self._cache_get(k)
                    if v is not None:
                        self._cache[k] = v
                        batch_cached[k] = v
                        idx = miss_keys.index(k)
                        miss_inputs.pop(idx)
                        miss_keys.pop(idx)

            if not miss_inputs:
                embeddings.extend([batch_cached[self._ckey(t)] for t in batch])
                continue

            payload = {"model": self.model, "input": miss_inputs}
            if self.rate_delay > 0:
                time.sleep(self.rate_delay)

            try:
                result = self._post_with_retry(payload)
                data = result.get("data") or []
                # 使用返回的 index 字段对齐；若缺失，则顺序回填
                remain_map: Dict[int, List[float]] = {}
                for item in data:
                    idx = item.get("index")
                    emb = item.get("embedding")
                    if idx is None:
                        remain_map[len(remain_map)] = emb
                    else:
                        remain_map[int(idx)] = emb

                merged: List[List[float]] = []
                next_idx = 0
                for t, k in zip(batch, keys):
                    if k in batch_cached:
                        merged.append(batch_cached[k])
                        continue
                    emb = remain_map.get(next_idx)
                    if emb is None:
                        logger.warning("API未返回足够的embedding，使用零向量兜底。")
                        emb = self._zero_vector()
                    else:
                        next_idx += 1
                    self._cache[k] = emb
                    self._cache_put(k, emb)
                    merged.append(emb)

                embeddings.extend(merged)
                
            except Exception as e:
                logger.error("批次 %d 生成失败: %s", (start // batch_size) + 1, e)
                embeddings.extend([self._zero_vector()] * len(batch))
        
        return embeddings

    # ---------- HTTP ----------
    def _build_session(self) -> requests.Session:
        s = requests.Session()
        retry = Retry(
            total=self.max_retry,
            read=self.max_retry,
            connect=self.max_retry,
            backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["POST"]),
            raise_on_status=False,
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=50)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        return s

    def _post_with_retry(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self._session.post(self._url, headers=self._headers, json=payload, timeout=self.timeout_sec)
                if resp.status_code == 429:
                    ra = resp.headers.get("Retry-After")
                    delay = float(ra) if ra and ra.isdigit() else min(8.0 * attempt, 30.0)
                    time.sleep(delay)
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.Timeout:
                if attempt >= self.max_retry:
                    raise TimeoutError("Embedding API请求超时")
            except requests.exceptions.RequestException as e:
                if attempt >= self.max_retry:
                    raise RuntimeError(f"Embedding API调用失败: {e}")
            sleep_s = min(1.5 * (2 ** (attempt - 1)), 10.0)
            time.sleep(sleep_s)

    # ---------- 缓存键/零向量 ----------
    def _ckey(self, text: str) -> str:
        h = hashlib.sha256()
        h.update(text.encode("utf-8"))
        return f"{h.hexdigest()}::{self.model}::{self.base_url}"

    def _zero_vector(self) -> List[float]:
        dim_env = os.getenv("EMBED_FALLBACK_DIM")
        if dim_env:
            try:
                d = int(dim_env)
                return [0.0] * d
            except Exception:
                pass
        return [0.0] * 3072

    # ---------- SQLite 持久化 ----------
    def _init_cache_db(self):
        try:
            from pathlib import Path
            Path(self.cache_db_path).parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.cache_db_path) as con:
                cur = con.cursor()
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS embed_cache (k TEXT PRIMARY KEY, ts INTEGER, dim INTEGER, v BLOB)"
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_ts ON embed_cache(ts)")
                cur.execute("PRAGMA journal_mode=WAL;")
                cur.execute("PRAGMA synchronous=NORMAL;")
                con.commit()
        except Exception as e:
            logger.warning("初始化缓存DB失败：%s", e)

    def _cache_get(self, key: str) -> Optional[List[float]]:
        try:
            with sqlite3.connect(self.cache_db_path) as con:
                cur = con.cursor()
                row = cur.execute("SELECT ts, dim, v FROM embed_cache WHERE k=?", (key,)).fetchone()
                if not row:
                    return None
                ts, dim, blob = row
                now = int(time.time())
                if self.cache_ttl_sec > 0 and now - int(ts) > self.cache_ttl_sec:
                    cur.execute("DELETE FROM embed_cache WHERE k=?", (key,))
                    con.commit()
                    return None
                if self.cache_binary:
                    import array
                    arr = array.array("f")
                    arr.frombytes(blob)
                    if dim and len(arr) != dim:
                        return None
                    return list(arr)
                else:
                    vec = json.loads(blob)
                    if dim and len(vec) != dim:
                        return None
                    return vec
        except Exception as e:
            logger.debug("读取缓存失败：%s", e)
            return None

    def _cache_put(self, key: str, emb: List[float]):
        try:
            with sqlite3.connect(self.cache_db_path) as con:
                cur = con.cursor()
                if self.cache_binary:
                    import array
                    arr = array.array("f", emb)
                    blob = arr.tobytes()
                else:
                    blob = json.dumps(emb)
                cur.execute(
                    "INSERT OR REPLACE INTO embed_cache (k, ts, dim, v) VALUES (?, ?, ?, ?)",
                    (key, int(time.time()), len(emb), blob),
                )
                if self.cache_max_entries > 0:
                    row = cur.execute("SELECT COUNT(1) FROM embed_cache").fetchone()
                    cnt = int(row[0]) if row else 0
                    if cnt > self.cache_max_entries:
                        to_del = cnt - self.cache_max_entries
                        cur.execute(
                            "DELETE FROM embed_cache WHERE k IN (SELECT k FROM embed_cache ORDER BY ts ASC LIMIT ?)",
                            (to_del,),
                        )
                con.commit()
        except Exception as e:
            logger.debug("写入缓存失败：%s", e)
