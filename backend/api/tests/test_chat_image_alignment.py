from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_align_answer_images_removes_orphaned_caption_blocks():
    import os

    os.environ["DEBUG"] = "true"
    from backend.api.routers.chat import _align_answer_images

    answer = (
        "### 资料印证\n\n"
        "这里先看第一张图。\n\n"
        "（图7：另一典型急诊室设计）\n"
        "[image:6]\n\n"
        "（图2：急诊室内部透视图）\n"
        "[image:1]"
    )
    image_refs = [
        {"url": "https://example.com/0.jpg", "caption": "图0"},
        {"url": "https://example.com/1.jpg", "caption": "图1"},
    ]

    aligned_answer, aligned_refs = _align_answer_images(answer, image_refs)

    assert "图7" not in aligned_answer
    assert "[image:6]" not in aligned_answer
    assert "图1：急诊室内部透视图" in aligned_answer
    assert "[image:0]" in aligned_answer
    assert aligned_refs == [image_refs[1]]


def test_align_answer_images_reorders_valid_placeholders_to_match_rendered_images():
    import os

    os.environ["DEBUG"] = "true"
    from backend.api.routers.chat import _align_answer_images

    answer = (
        "### 关键洞察\n\n"
        "先看整体。\n\n"
        "（图4：总体平面）\n"
        "[image:3]\n\n"
        "再看节点。\n\n"
        "（图2：局部详图）\n"
        "[image:1]"
    )
    image_refs = [
        {"url": "https://example.com/0.jpg", "caption": "图0"},
        {"url": "https://example.com/1.jpg", "caption": "图1"},
        {"url": "https://example.com/2.jpg", "caption": "图2"},
        {"url": "https://example.com/3.jpg", "caption": "图3"},
    ]

    aligned_answer, aligned_refs = _align_answer_images(answer, image_refs)

    assert "图1：总体平面" in aligned_answer
    assert "图2：局部详图" in aligned_answer
    assert "[image:0]" in aligned_answer
    assert "[image:1]" in aligned_answer
    assert aligned_refs == [image_refs[3], image_refs[1]]


def test_append_image_placeholders_appends_missing_images_instead_of_stopping_at_first_token():
    import os

    os.environ["DEBUG"] = "true"
    from backend.api.routers.chat import _append_image_placeholders

    answer = "### 资料印证\n\n已有首张图。\n\n（图1：总体平面）\n[image:0]"
    image_refs = [
        {"url": "https://example.com/0.jpg", "caption": "总体平面"},
        {"url": "https://example.com/1.jpg", "caption": "局部详图"},
        {"url": "https://example.com/2.jpg", "caption": "设备接口"},
    ]

    appended = _append_image_placeholders(answer, image_refs)

    assert appended.count("[image:0]") == 1
    assert "[image:1]" in appended
    assert "[image:2]" in appended
    assert "图2：局部详图" in appended
    assert "图3：设备接口" in appended


def test_extract_image_refs_returns_all_images_when_not_capped():
    import os

    os.environ["DEBUG"] = "true"
    from backend.api.routers.chat import _extract_image_refs

    citations = [
        {"image_url": f"https://example.com/{index}.jpg", "source": f"doc-{index}", "snippet": f"图{index}"}
        for index in range(6)
    ]

    refs = _extract_image_refs(citations, "http://localhost:8000")

    assert len(refs) == 6
