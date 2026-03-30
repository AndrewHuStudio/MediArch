from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.agents.online_search_policy import decide_online_search_usage


def test_deep_search_enables_online_search():
    decision = decide_online_search_usage(
        query="门诊大厅设计案例",
        include_online_search=False,
        deep_search=True,
        thinking_mode=False,
    )

    assert decision["enabled"] is True
    assert decision["search_mode"] == "deep_search"
    assert decision["reason"] == "deep_search"


def test_default_query_does_not_enable_online_search():
    decision = decide_online_search_usage(
        query="门诊大厅设计要点",
        include_online_search=False,
        deep_search=False,
        thinking_mode=False,
    )

    assert decision == {
        "enabled": False,
        "search_mode": "supplement",
        "reason": "disabled",
    }


def test_time_sensitive_query_enables_online_search_without_deep_search():
    decision = decide_online_search_usage(
        query="最新医院门诊大厅设计趋势有哪些",
        include_online_search=False,
        deep_search=False,
        thinking_mode=False,
    )

    assert decision["enabled"] is True
    assert decision["search_mode"] == "supplement"
    assert decision["reason"] == "time_sensitive_query"
