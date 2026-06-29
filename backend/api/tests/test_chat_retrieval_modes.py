from pathlib import Path

from backend.api.routers import documents
from backend.api.schemas.chat import ChatRequest
from backend.api.routers.chat import (
    _align_answer_images,
    _build_image_caption,
    _filter_image_refs_for_answer,
    _inject_image_placeholders_inline,
    _sanitize_image_tokens,
)


def test_chat_request_accepts_external_baseline_modes():
    bm25 = ChatRequest(message="test", retrieval_mode="BM25")
    vrag = ChatRequest(message="test", retrieval_mode="VRAG")

    assert bm25.retrieval_mode == "BM25"
    assert vrag.retrieval_mode == "VRAG"


def test_chat_request_accepts_source_metadata():
    request = ChatRequest(
        message="test",
        retrieval_mode="R2",
        metadata={
            "question_id": "Q019",
            "source_type": "policy_document",
            "task_type": "fact",
            "retrieval_mode": "VRAG",
        },
    )

    assert request.metadata["question_id"] == "Q019"
    assert request.metadata["source_type"] == "policy_document"
    assert request.retrieval_mode == "R2"


def test_image_path_resolution_supports_legacy_missing_category_layout(tmp_path, monkeypatch):
    ocr_root = tmp_path / "documents_ocr"
    image_path = ocr_root / "书籍报告" / "医疗功能房间详图集3" / "full" / "images" / "page_10.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"fake")

    monkeypatch.setattr(documents, "OCR_OUTPUT_DIR", ocr_root)
    resolved = documents._resolve_image_candidate("医疗功能房间详图集3/full/images/page_10.png")

    assert resolved == image_path


def test_backend_repositions_llm_image_tokens_under_matching_paragraphs():
    answer = (
        "### 功能分区与公共空间设计\n"
        "门诊单元应围绕候诊、诊室和公共空间组织流线。\n\n"
        "### 诊室设计\n"
        "诊室应保障一医一患、私密性和基本诊疗功能。\n\n"
        "（图1：模型放在末尾的门诊大厅图）\n"
        "[image:0]"
    )
    image_refs = [
        {
            "url": "/api/v1/documents/image?path=outpatient-hall.png",
            "caption": "门诊大厅实景与公共空间效果图",
            "source": "医院建筑设计指南.pdf",
        }
    ]

    result = _inject_image_placeholders_inline(answer, image_refs, force_reposition=True)

    assert result.count("[image:0]") == 1
    assert "模型放在末尾" not in result
    assert result.index("[image:0]") < result.index("### 诊室设计")
    assert result.index("[image:0]") < result.index("图1 ")


def test_backend_places_each_image_under_the_most_relevant_section():
    answer = (
        "### 功能分区与公共空间设计\n"
        "公共空间包括门诊大厅、候诊区和交通组织。\n\n"
        "### 诊室设计\n"
        "诊室设计强调私密性、医生工作区和患者就诊流线。\n"
    )
    image_refs = [
        {"url": "/img/hall.png", "caption": "门诊大厅实景与候诊公共空间"},
        {"url": "/img/clinic.png", "caption": "诊室平面布置与医生患者位置"},
    ]

    result = _inject_image_placeholders_inline(answer, image_refs, force_reposition=True)

    assert result.index("[image:0]") < result.index("### 诊室设计")
    assert result.index("[image:1]") > result.index("### 诊室设计")


def test_image_caption_is_short_and_hides_raw_vlm_analysis():
    caption = _build_image_caption(
        {
            "source": "既有大型综合医院门诊部功能布局优化设计研究_吴俊.pdf",
            "page_number": 2,
            "snippet": "1门诊部功能布局优化设计需求分析 根据所提供的图像及上下文信息，现从医院建筑设计专家视角进行专业分析如下：—— 1. **图片类型**：实景。",
        }
    )

    assert len(caption) <= 18
    assert ".pdf" not in caption
    assert "专业分析" not in caption
    assert "图片类型" not in caption
    assert caption.endswith("图")


def test_backend_uses_match_text_not_short_caption_for_image_placement():
    answer = (
        "### 功能分区与公共空间设计\n"
        "公共空间包括门诊大厅、候诊区和交通组织。\n\n"
        "### 诊室设计\n"
        "诊室设计强调私密性、医生工作区和患者就诊流线。\n"
    )
    image_refs = [
        {
            "url": "/img/hall.png",
            "caption": "图示",
            "match_text": "门诊大厅 实景 候诊 公共空间",
        }
    ]

    result = _inject_image_placeholders_inline(answer, image_refs, force_reposition=True)

    assert result.index("[image:0]") < result.index("### 诊室设计")


def test_image_injection_does_not_emit_relation_summary_banner():
    answer = "### 功能分区\n门诊大厅承担候诊与导引功能。"
    image_refs = [
        {"url": "/img/hall.png", "caption": "门诊大厅图示", "role": "overall"},
        {"url": "/img/detail.png", "caption": "节点详图", "role": "detail"},
    ]

    result = _inject_image_placeholders_inline(answer, image_refs, force_reposition=True)

    assert "图示关系" not in result


def test_image_caption_is_below_image_token_and_uses_simple_format():
    answer = "### 诊室设计\n单人诊室应保障私密性和基本诊疗流程。"
    image_refs = [{"url": "/img/clinic.png", "caption": "单人诊室图", "match_text": "单人诊室 私密性"}]

    result = _inject_image_placeholders_inline(answer, image_refs, force_reposition=True)

    assert "[image:0]\n图1 单人诊室图" in result
    assert "（图1" not in result


def test_image_is_inserted_after_specific_matching_sentence_not_section_title_only():
    answer = (
        "### 门诊单元设计要点\n"
        "总体布局应靠近主入口并便于识别。\n\n"
        "候诊区需要结合门诊大厅组织导引、排队和公共服务。\n\n"
        "诊室应保障一医一患和私密性。\n"
    )
    image_refs = [{"url": "/img/hall.png", "caption": "门诊大厅图", "match_text": "门诊大厅 候诊区 排队 公共服务"}]

    result = _inject_image_placeholders_inline(answer, image_refs, force_reposition=True)

    assert result.index("候诊区需要") < result.index("[image:0]")
    assert result.index("[image:0]") < result.index("诊室应保障")


def test_image_placement_pairs_semantic_match_text_with_related_paragraph():
    answer = (
        "### 总体原则\n"
        "门诊空间需要先明确入口、服务台、候诊和诊室之间的组织关系。\n\n"
        "### 候诊与导引\n"
        "候诊厅应承担到达后的分流、等候、咨询和排队组织，服务台需要保持清晰可见。\n\n"
        "### 诊疗单元\n"
        "诊室更关注一医一患、私密性和医生患者的基本操作距离。"
    )
    image_refs = [
        {
            "url": "/img/waiting-routing.png",
            "caption": "公共空间图",
            "match_text": "门诊大厅 候诊区 导医台 咨询台 分诊 排队 导向 标识 公共服务",
            "source": "医院建筑设计指南.pdf",
        }
    ]

    result = _inject_image_placeholders_inline(answer, image_refs, force_reposition=True)

    assert "[image:0]" in result
    assert result.index("候诊厅应承担") < result.index("[image:0]")
    assert result.index("[image:0]") < result.index("### 诊疗单元")


def test_image_placement_understands_common_design_synonyms():
    answer = (
        "### 候诊与导引\n"
        "服务台应靠近患者到达后的主要流线，结合导引信息帮助患者完成分流和等候。\n\n"
        "### 诊疗单元\n"
        "诊室更关注一医一患、私密性和医生患者的基本操作距离。"
    )
    image_refs = [
        {
            "url": "/img/wayfinding.png",
            "caption": "公共空间图",
            "match_text": "导医台 动线 标识系统 分诊 候诊区",
            "source": "医院建筑设计指南.pdf",
        }
    ]

    result = _inject_image_placeholders_inline(answer, image_refs, force_reposition=True)

    assert "[image:0]" in result
    assert result.index("[image:0]") < result.index("### 诊疗单元")


def test_unrelated_images_are_not_inserted_when_backend_repositions():
    answer = (
        "### 诊室设计\n"
        "诊室应保障一医一患、私密性和基本诊疗流程。\n\n"
        "### 门诊公共空间\n"
        "候诊区需要组织导引、排队和公共服务。\n"
    )
    image_refs = [
        {"url": "/img/clinic.png", "caption": "单人诊室图", "match_text": "单人诊室 私密性 诊疗"},
        {"url": "/img/chart.png", "caption": "统计柱状图", "match_text": "2007 2008 人次 增长率 统计"},
    ]

    result = _inject_image_placeholders_inline(answer, image_refs, force_reposition=True)

    assert "[image:0]" in result
    assert "[image:1]" not in result


def test_alignment_drops_image_refs_that_were_not_inserted_in_answer():
    answer = "诊室应保障一医一患和私密性。\n\n[image:0]\n图1 单人诊室图"
    image_refs = [
        {"url": "/img/clinic.png", "caption": "单人诊室图"},
        {"url": "/img/chart.png", "caption": "统计柱状图"},
    ]

    aligned_answer, aligned_refs = _align_answer_images(answer, image_refs)

    assert "[image:0]" in aligned_answer
    assert len(aligned_refs) == 1
    assert aligned_refs[0]["url"] == "/img/clinic.png"


def test_image_refs_are_filtered_by_question_and_answer_relevance():
    query = "门诊诊室如何保障一医一患和私密性？"
    answer = "诊室应保障一医一患、基本诊疗流程和患者隐私。"
    image_refs = [
        {"url": "/img/clinic.png", "caption": "单人诊室图", "match_text": "单人诊室 一医一患 私密性"},
        {"url": "/img/lobby.png", "caption": "医院大厅图", "match_text": "医院大厅 中庭 商业 公共空间"},
        {"url": "/img/chart.png", "caption": "统计柱状图", "match_text": "年度门诊量 人次 增长率 统计"},
    ]

    filtered = _filter_image_refs_for_answer(image_refs, query=query, answer=answer)

    assert [ref["url"] for ref in filtered] == ["/img/clinic.png"]


def test_sanitize_image_tokens_removes_placeholder_writing():
    # LLM 照抄 prompt 占位写法 [image:i] / [image:序号] / [image:i0],应被清除。
    answer = "诊室设计要点。\n\n[image:i]\n图1 诊室图\n\n候诊区设计。\n\n[image:序号]\n\n[image:i0]"
    cleaned = _sanitize_image_tokens(answer, 2)
    assert "[image:i]" not in cleaned
    assert "[image:序号]" not in cleaned
    assert "[image:i0]" not in cleaned


def test_sanitize_image_tokens_keeps_valid_and_drops_out_of_range():
    answer = "段落甲。\n\n[image:0]\n\n段落乙。\n\n[image:5]"
    cleaned = _sanitize_image_tokens(answer, 2)
    assert "[image:0]" in cleaned  # 合法且在范围内,保留
    assert "[image:5]" not in cleaned  # 越界,清除


def test_sanitize_image_tokens_handles_empty():
    assert _sanitize_image_tokens("", 3) == ""
    assert _sanitize_image_tokens("没有图片 token 的正文", 0) == "没有图片 token 的正文"
