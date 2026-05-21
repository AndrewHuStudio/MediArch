from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from backend.app.agents.result_synthesizer_agent.agent import (
    _collapse_inline_whitespace_preserving_indentation,
)


def test_collapse_inline_whitespace_preserves_nested_ordered_list_indentation():
    markdown = (
        "1. 一级   条目\n\n"
        "   1. 二级   条目\n\n"
        "      1. 三级   条目\n\n"
        "   - 二级   无序\n\n"
        "1. 同级   一级\n"
    )

    cleaned = _collapse_inline_whitespace_preserving_indentation(markdown)

    assert cleaned == (
        "1. 一级 条目\n\n"
        "   1. 二级 条目\n\n"
        "      1. 三级 条目\n\n"
        "   - 二级 无序\n\n"
        "1. 同级 一级\n"
    )
