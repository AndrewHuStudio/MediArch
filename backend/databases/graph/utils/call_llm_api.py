import os
import time
import uuid
from typing import List, Dict, Any, Optional

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class LLMClient:
    """Unified LLM wrapper with retries, backoff, timeout, and simple cost logging."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ):
        self.api_key = api_key or os.getenv("KG_OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("KG_OPENAI_BASE_URL")
        self.model = model or os.getenv("KG_OPENAI_MODEL", "gpt-4o-mini")
        timeout_env = os.getenv("KG_OPENAI_TIMEOUT")
        self.request_timeout = (
            request_timeout
            or (float(timeout_env) if timeout_env not in {None, ""} else 120.0)
        )
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def chat_json(self, messages: List[Dict[str, str]], temperature: float = 0.1, max_retries: int = 3, retry_base: float = 1.2, trace_id: Optional[str] = None, request_timeout: Optional[float] = None) -> Dict[str, Any]:
        """Chat and parse JSON with retries. Returns dict (empty on failure)."""
        trace = trace_id or str(uuid.uuid4())[:8]
        last_err: Optional[Exception] = None
        timeout = request_timeout or self.request_timeout
        for attempt in range(max_retries):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    response_format={"type": "json_object"},
                    timeout=timeout,
                )
                txt = resp.choices[0].message.content
                import json
                return json.loads(txt or "{}")
            except Exception as e:
                last_err = e
                # minimal jittered backoff
                sleep_s = (retry_base ** attempt) + 0.1 * attempt
                time.sleep(sleep_s)
        # Failed
        if last_err:
            print(f"[ERROR] LLM chat_json failed after {max_retries} attempts: {last_err}")
        return {}

    def embeddings(self, texts: List[str], model: Optional[str] = None, dimensions: Optional[int] = None, max_retries: int = 3) -> List[List[float]]:
        out: List[List[float]] = []
        emb_model = model or os.getenv("EMBEDDING_MODEL")
        for t in texts:
            last_err = None
            for attempt in range(max_retries):
                try:
                    resp = self.client.embeddings.create(
                        model=emb_model,
                        input=t,
                        dimensions=dimensions,
                        timeout=self.request_timeout,
                    )
                    out.append(resp.data[0].embedding)
                    break
                except Exception as e:
                    last_err = e
                    time.sleep(0.8 * (attempt + 1))
            else:
                if last_err:
                    print(f"[ERROR] Embedding generation failed: {last_err}")
                out.append([])
        return out


