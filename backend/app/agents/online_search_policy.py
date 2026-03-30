from __future__ import annotations

from typing import Any, Dict


_TIME_SENSITIVE_TERMS = (
    "最新",
    "近期",
    "当前",
    "趋势",
    "前沿",
    "recent",
    "latest",
    "current",
    "trend",
    "trends",
)


def decide_online_search_usage(
    query: str,
    *,
    include_online_search: bool,
    deep_search: bool,
    thinking_mode: bool,
) -> Dict[str, Any]:
    normalized_query = str(query or "").strip().lower()

    if deep_search:
        return {
            "enabled": True,
            "search_mode": "deep_search",
            "reason": "deep_search",
        }

    if thinking_mode:
        return {
            "enabled": True,
            "search_mode": "deep_search",
            "reason": "thinking_mode",
        }

    if include_online_search:
        return {
            "enabled": True,
            "search_mode": "supplement",
            "reason": "explicit_request",
        }

    if any(term in normalized_query for term in _TIME_SENSITIVE_TERMS):
        return {
            "enabled": True,
            "search_mode": "supplement",
            "reason": "time_sensitive_query",
        }

    return {
        "enabled": False,
        "search_mode": "supplement",
        "reason": "disabled",
    }
