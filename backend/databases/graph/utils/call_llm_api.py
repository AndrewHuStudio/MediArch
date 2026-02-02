import os
import time
import uuid
import json
import re
import ast
from typing import List, Dict, Any, Optional

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class LLMClient:
    """Unified LLM wrapper with retries, backoff, timeout, and simple cost logging."""

    _DEFAULT_JSON_SYSTEM_PROMPT = (
        "You are a JSON API. Reply with ONLY a valid JSON object and nothing else. "
        "Do not wrap in markdown code fences. Do not add explanations."
    )

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

    @staticmethod
    def _iter_json_candidates(text: str):
        """Yield possible JSON substrings from a messy LLM response."""
        if not text:
            return

        stripped = text.strip()
        if stripped:
            yield stripped

        # Common pattern: ```json { ... } ```
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
        if fence:
            fenced_body = (fence.group(1) or "").strip()
            if fenced_body:
                yield fenced_body

        # Heuristic: extract the first balanced {...} or [...] span.
        for open_char, close_char in (("{", "}"), ("[", "]")):
            start = text.find(open_char)
            if start < 0:
                continue

            depth = 0
            in_string = False
            escape = False
            for idx in range(start, len(text)):
                ch = text[idx]
                if escape:
                    escape = False
                    continue
                if ch == "\\":
                    if in_string:
                        escape = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == open_char:
                    depth += 1
                elif ch == close_char:
                    depth -= 1
                    if depth == 0:
                        candidate = text[start : idx + 1].strip()
                        if candidate:
                            yield candidate
                        break

    @classmethod
    def _parse_json_object(cls, text: str) -> Dict[str, Any]:
        """Parse a JSON object from text. Returns {} if parsing fails."""
        for candidate in cls._iter_json_candidates(text):
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass

            # Fallback: some models return Python dict repr with single quotes.
            try:
                parsed = ast.literal_eval(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass

        return {}

    def chat_json(self, messages: List[Dict[str, str]], temperature: float = 0.1, max_retries: int = 3, retry_base: float = 1.2, trace_id: Optional[str] = None, request_timeout: Optional[float] = None) -> Dict[str, Any]:
        """Chat and parse JSON with retries. Returns dict (empty on failure)."""
        trace = trace_id or str(uuid.uuid4())[:8]
        last_err: Optional[Exception] = None
        timeout = request_timeout or self.request_timeout
        for attempt in range(max_retries):
            try:
                # Some OpenAI-compatible providers ignore `response_format`. Add a system
                # guardrail and use tolerant parsing as a fallback.
                final_messages = messages
                if not final_messages or final_messages[0].get("role") != "system":
                    final_messages = [{"role": "system", "content": self._DEFAULT_JSON_SYSTEM_PROMPT}] + list(messages or [])

                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=final_messages,
                    temperature=temperature,
                    response_format={"type": "json_object"},
                    timeout=timeout,
                )
                txt = resp.choices[0].message.content or ""
                parsed = self._parse_json_object(txt)
                if parsed:
                    return parsed
                raise ValueError("LLM did not return a JSON object")
            except Exception as e:
                last_err = e
                # minimal jittered backoff
                sleep_s = (retry_base ** attempt) + 0.1 * attempt
                time.sleep(sleep_s)
        # Failed
        if last_err:
            print(f"[ERROR] LLM chat_json failed after {max_retries} attempts (trace={trace}): {last_err}")
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

