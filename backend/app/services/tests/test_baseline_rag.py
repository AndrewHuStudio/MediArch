from backend.app.services.baseline_rag import (
    build_bm25_ranked_chunks,
    chunk_to_agent_item,
    tokenize_bm25_text,
)


def test_tokenize_bm25_text_handles_chinese_terms_and_ascii():
    tokens = tokenize_bm25_text("ICU护理单元需要独立通道")

    assert "icu" in tokens
    assert "护理单元" in tokens
    assert "独立通道" in tokens


def test_build_bm25_ranked_chunks_prefers_lexical_match():
    chunks = [
        {
            "chunk_id": "c1",
            "chunk_text": "门诊大厅应组织候诊空间和导向系统。",
            "source_document": "门诊指南",
        },
        {
            "chunk_id": "c2",
            "chunk_text": "住院部护理单元护士站应通视护理单元走廊。",
            "source_document": "综合医院建筑设计规范",
        },
        {
            "chunk_id": "c3",
            "chunk_text": "影像中心需要控制检查流线。",
            "source_document": "影像中心设计",
        },
    ]

    ranked = build_bm25_ranked_chunks("护理单元护士站", chunks, limit=2)

    assert [row["chunk_id"] for row in ranked] == ["c2", "c1"]
    assert ranked[0]["bm25_score"] > ranked[1]["bm25_score"]


def test_chunk_to_agent_item_preserves_citation_fields():
    chunk = {
        "chunk_id": "c42",
        "chunk_text": "护士站到最远病房门口距离不宜超过30m。",
        "source_document": "综合医院建筑设计规范",
        "section": "5.5.6",
        "page_range": [12, 12],
        "content_type": "text",
        "doc_id": "doc-1",
        "bm25_score": 3.14,
    }

    item = chunk_to_agent_item(chunk, source="bm25_baseline")

    assert item.entity_id == "c42"
    assert item.source == "bm25_baseline"
    assert item.score == 3.14
    assert item.citations[0]["source"] == "综合医院建筑设计规范"
    assert item.citations[0]["page_number"] == 12
    assert item.attrs["retrieval_score_type"] == "bm25"
