from pathlib import Path
import os
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_postprocess_answer_preserves_nested_ordered_list_indentation():
    os.environ["DEBUG"] = "true"
    from backend.api.routers.chat import _postprocess_answer_and_align_citations

    answer = (
        "1. 一级   条目[1]\n\n"
        "   1. 二级   条目[1]\n\n"
        "      1. 三级   条目[1]\n\n"
        "   - 二级   无序[1]\n\n"
        "1. 同级   一级[1]\n"
    )
    citations = [
        {
            "source": "测试资料",
            "page_number": 1,
            "snippet": "测试片段",
        }
    ]

    cleaned, normalized_citations = _postprocess_answer_and_align_citations(
        answer,
        citations,
        include_citations=True,
    )

    assert "   1. 二级 条目" in cleaned
    assert "      1. 三级 条目" in cleaned
    assert "   - 二级 无序" in cleaned
    assert normalized_citations == citations


def test_postprocess_answer_keeps_same_level_and_nested_numbering_stable():
    os.environ["DEBUG"] = "true"
    from backend.api.routers.chat import _postprocess_answer_and_align_citations

    answer = (
        "1. **强化地下空间利用率：**[1]\n\n"
        "   - 地下一层可增加多功能区。[1]\n\n"
        "   - 地下二、三层引入智能交通系统。[1]\n\n"
        "1. **垂直生态整合：**[1]\n\n"
        "   1. 地面层结合立体绿化策略。[1]\n\n"
        "   2. 场所剖示图可扩展为动态模型。[1]\n\n"
        "1. **流程数字化升级：**[1]\n\n"
        "   - 将图号系统集成 BIM 平台。[1]\n"
    )
    citations = [
        {
            "source": "测试资料",
            "page_number": 1,
            "snippet": "测试片段",
        }
    ]

    cleaned, _ = _postprocess_answer_and_align_citations(
        answer,
        citations,
        include_citations=True,
    )

    assert cleaned.count("1. **") == 3
    assert "\n\n   1. 地面层结合立体绿化策略。" in cleaned
    assert "\n\n   2. 场所剖示图可扩展为动态模型。" in cleaned
    assert "\n\n1. **流程数字化升级：**" in cleaned
