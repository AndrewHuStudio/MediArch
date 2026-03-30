"""
Reranker 集成（本地/API 双模式）。

支持:
- 本地模型: FlagEmbedding / sentence-transformers CrossEncoder
- API 模式: OpenAI 兼容网关（/rerank）
"""

import os
import logging
from typing import List, Dict, Any, Optional
from backend.llm_env import get_api_key, get_llm_base_url

logger = logging.getLogger(__name__)


class BgeReranker:
    """Cross-encoder reranking (local or API)."""

    def __init__(
        self,
        model_name: Optional[str] = None,
        device: Optional[str] = None,
        use_api: Optional[bool] = None,
    ):
        self.model_name = model_name or os.getenv(
            "RERANKER_MODEL", "qwen3-reranker-8b"
        )
        self.device = device or os.getenv("RERANKER_DEVICE", "cpu")
        use_api_env = os.getenv("RERANKER_USE_API")
        if use_api is not None:
            self.use_api = bool(use_api)
        elif use_api_env is not None:
            self.use_api = use_api_env.lower() in {"1", "true", "yes", "on"}
        else:
            # 显式未配置时，若存在 API URL 则自动启用 API 模式。
            self.use_api = bool(
                os.getenv("RERANKER_API_URL")
                or os.getenv("RERANKER_API_BASE_URL")
                or get_llm_base_url()
            )
        self._model = None

    @staticmethod
    def _resolve_api_url() -> str:
        api_url = (os.getenv("RERANKER_API_URL") or "").strip().rstrip("/")
        if api_url:
            return api_url

        api_base = (
            os.getenv("RERANKER_API_BASE_URL")
            or get_llm_base_url()
            or ""
        ).strip().rstrip("/")
        if not api_base:
            return ""
        return f"{api_base}/rerank"

    def _load_model(self):
        if self._model is not None:
            return
        if self.use_api:
            return
        try:
            from FlagEmbedding import FlagReranker
            self._model = FlagReranker(
                self.model_name,
                use_fp16=(self.device != "cpu"),
            )
            logger.info("Loaded FlagReranker: %s on %s", self.model_name, self.device)
        except ImportError:
            try:
                from sentence_transformers import CrossEncoder
                self._model = CrossEncoder(self.model_name, device=self.device)
                logger.info("Loaded CrossEncoder: %s on %s", self.model_name, self.device)
            except ImportError:
                raise ImportError(
                    "Reranker requires FlagEmbedding or sentence-transformers. "
                    "Install via: pip install FlagEmbedding  or  pip install sentence-transformers"
                )

    def rerank(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        top_k: int = 10,
    ) -> List[Dict[str, Any]]:
        """Rerank chunks by relevance to query.

        Args:
            query: The query string
            chunks: List of dicts with "content" key
            top_k: Return top-k results

        Returns:
            Sorted list of chunks with "rerank_score" added
        """
        if not chunks or not query:
            return chunks[:top_k]

        if self.use_api:
            return self._rerank_via_api(query, chunks, top_k)

        self._load_model()
        pairs = [(query, c.get("content", "")) for c in chunks]

        if hasattr(self._model, "compute_score"):
            scores = self._model.compute_score(pairs)
            if isinstance(scores, (int, float)):
                scores = [scores]
        else:
            scores = self._model.predict(pairs).tolist()

        for chunk, score in zip(chunks, scores):
            chunk["rerank_score"] = float(score)

        ranked = sorted(chunks, key=lambda c: c.get("rerank_score", 0), reverse=True)
        return ranked[:top_k]

    def _rerank_via_api(
        self, query: str, chunks: List[Dict[str, Any]], top_k: int
    ) -> List[Dict[str, Any]]:
        """Rerank via external API endpoint."""
        import requests

        api_url = self._resolve_api_url()
        api_key = (
            os.getenv("RERANKER_API_KEY")
            or get_api_key()
            or ""
        )
        if not api_url:
            logger.warning("RERANKER API URL not set, returning chunks as-is")
            return chunks[:top_k]

        documents = [c.get("content", "") for c in chunks]
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            resp = requests.post(
                api_url,
                headers=headers,
                json={
                    "query": query,
                    "documents": documents,
                    "top_n": top_k,
                    "model": self.model_name,
                },
                timeout=int(os.getenv("RERANKER_TIMEOUT", "30")),
            )
            resp.raise_for_status()
            payload = resp.json() or {}
            results = payload.get("results")
            if not isinstance(results, list):
                # 兼容 OpenAI 风格返回: { "data": [ ... ] }
                data = payload.get("data")
                results = data if isinstance(data, list) else []

            if not results:
                logger.warning("Reranker API returned empty results")
                return chunks[:top_k]

            for item in results:
                idx = item.get("index", 0)
                if 0 <= idx < len(chunks):
                    score = (
                        item.get("relevance_score")
                        if item.get("relevance_score") is not None
                        else item.get("score")
                    )
                    if score is None:
                        score = item.get("similarity", 0)
                    chunks[idx]["rerank_score"] = float(score or 0.0)
        except Exception as e:
            logger.error("Reranker API call failed: %s", e)
            return chunks[:top_k]

        ranked = sorted(chunks, key=lambda c: c.get("rerank_score", 0), reverse=True)
        return ranked[:top_k]
