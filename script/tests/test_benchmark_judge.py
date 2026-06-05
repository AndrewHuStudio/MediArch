import pytest

from script import benchmark_judge


def test_parse_judge_response_accepts_fenced_json():
    parsed = benchmark_judge.parse_judge_response(
        """```json
        {
          "evidence_hit": 1,
          "accuracy": 2,
          "completeness": 1,
          "unsupported_claim": 0,
          "rationale": "命中了规范条文，但完整性略有缺口。"
        }
        ```"""
    )

    assert parsed["evidence_hit"] == "1"
    assert parsed["accuracy"] == "2"
    assert parsed["completeness"] == "1"
    assert parsed["unsupported_claim"] == "0"
    assert parsed["rationale"].startswith("命中")


def test_parse_judge_response_rejects_out_of_range_scores():
    with pytest.raises(ValueError, match="accuracy"):
        benchmark_judge.parse_judge_response(
            '{"evidence_hit": 1, "accuracy": 3, "completeness": 2, "unsupported_claim": 0, "rationale": "bad"}'
        )


def test_build_judge_messages_operationalizes_review_metrics():
    question = {
        "question_id": "Q001",
        "question": "护理单元如何控制护士站距离？",
        "gold_reference_docs": "GB 51039-2014 综合医院建筑设计规范",
        "gold_reference_sections": "第5.5.6条",
        "gold_evidence": "护士站到最远病房门口距离不宜超过30m。",
        "gold_answer": "应说明30m服务半径和通视护理单元走廊。",
        "judge_rubric": "Evidence_Hit is binary. Accuracy is 0/1/2. Completeness is 0/1/2.",
    }
    run = {
        "system_id": "R2",
        "answer": "护士站应位于护理单元中心，并控制最远病房门口距离不超过30m。",
        "citations": '[{"source":"GB 51039-2014 综合医院建筑设计规范","section":"第5.5.6条"}]',
    }

    messages = benchmark_judge.build_judge_messages(question, run, judge_id="A")
    content = messages[-1]["content"]

    assert "Evidence_Hit" in content
    assert "Accuracy" in content
    assert "Completeness" in content
    assert "护士站到最远病房门口距离不宜超过30m" in content
    assert "JSON" in content
