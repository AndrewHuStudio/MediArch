import pytest
from pathlib import Path

from script import benchmark_judge, benchmark_pipeline


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


def test_build_judge_messages_uses_v2_nonexclusive_gold_standard():
    question = {
        "question_id": "Q001",
        "question": "护理单元如何平衡效率与合规？",
        "gold_reference_docs": "GB 51039-2014 综合医院建筑设计规范",
        "gold_reference_sections": "第5.5.6条",
        "gold_evidence": "护士站到最远病房门口距离不宜超过30m。",
        "gold_answer": "应说明服务半径、通视关系和空间合规。",
        "judge_rubric": "Evidence support, accuracy, completeness, unsupported claims.",
    }
    run = {
        "system_id": "R2",
        "answer": "答案使用另一份设计指南说明护士站应靠近护理核心并缩短服务路径。",
        "citations": '[{"source":"医院建筑设计指南","snippet":"护士站分组负责病床，强调护理路径效率。"}]',
    }

    messages = benchmark_judge.build_judge_messages(question, run, judge_id="A")
    combined = "\n".join(message["content"] for message in messages)

    assert "reference anchor, not an exclusive answer key" in combined
    assert "Do not set Accuracy or Completeness to zero solely because" in combined
    assert "Gold Match" not in combined


def test_build_judge_messages_calibrates_design_inference_boundary():
    question = {
        "question_id": "Q002",
        "question": "在门急诊公共区域如何通过空间尺度与分区降低交叉感染风险？",
        "gold_reference_docs": "GB 51039-2014 综合医院建筑设计规范",
        "gold_reference_sections": "门急诊与公共区域相关条文",
        "gold_evidence": "应组织清晰流线、控制交叉感染并保障急救通畅。",
        "gold_answer": "应覆盖证据依据、空间约束、设计回应和必要的设计判断。",
        "judge_rubric": "Recommendation questions require evidence-grounded design guidance.",
    }
    run = {
        "system_id": "R2",
        "answer": "证据依据：门急诊应组织清晰流线[1]。\n空间约束：人流集中区域需要降低交叉接触。\n设计回应：建议分区组织候诊、急救和普通就诊。\n推论边界：这是基于流线证据的设计判断，不是规范原文。",
        "citations": '[{"source":"GB 51039-2014 综合医院建筑设计规范","snippet":"门急诊应组织清晰流线"}]',
    }

    messages = benchmark_judge.build_judge_messages(question, run, judge_id="A")
    combined = "\n".join(message["content"] for message in messages)

    assert "design inference" in combined
    assert "推论边界" in combined
    assert "should not be penalized as Unsupported_Claim solely because" in combined


def test_judge_timeout_zero_means_unlimited():
    assert benchmark_judge.normalize_timeout(0) is None
    assert benchmark_judge.normalize_timeout(-1) is None
    assert benchmark_judge.normalize_timeout(86400) == 86400


def test_judge_runs_passes_configured_timeout_to_model(monkeypatch):
    workspace_tmp = Path(".tmp") / "test_benchmark_judge_timeout"
    workspace_tmp.mkdir(parents=True, exist_ok=True)
    questions_path = workspace_tmp / "questions.csv"
    runs_path = workspace_tmp / "runs.csv"
    output_path = workspace_tmp / "judgments.csv"
    if output_path.exists():
        output_path.unlink()
    question = {
        "question_id": "Q001",
        "source_type": "technical_standard",
        "task_type": "fact",
        "difficulty": "easy",
        "question": "护士站距离要求是什么？",
        "gold_reference_docs": "GB 51039-2014",
        "gold_reference_sections": "第5.5.6条",
        "gold_evidence": "护士站到最远病房门口距离不宜超过30m。",
        "gold_answer": "不宜超过30m。",
        "judge_rubric": "Evidence support, accuracy, completeness, unsupported claims.",
        "answerability": "answerable",
        "status": "ready",
        "notes": "",
    }
    run = {
        "question_id": "Q001",
        "system_id": "R2",
        "system_label": "full_mediarch",
        "run_status": "done",
        "answer": "护士站到最远病房门口距离不宜超过30m。",
        "citations": "[]",
        "latency_s": "1.00",
        "retrieved_doc_ids": "",
        "retrieved_chunk_ids": "",
        "response_took_ms": "1000",
        "error": "",
    }
    benchmark_pipeline.write_csv(questions_path, benchmark_pipeline.QUESTION_FIELDS, [question])
    benchmark_pipeline.write_csv(runs_path, benchmark_pipeline.RUN_FIELDS, [run])

    observed = {}

    def fake_call_judge_model(messages, *, model=None, timeout=90):
        observed["timeout"] = timeout
        return '{"evidence_hit": 1, "accuracy": 2, "completeness": 2, "unsupported_claim": 0, "rationale": "ok"}'

    monkeypatch.setattr(benchmark_judge, "_load_questions", lambda path: {"Q001": question})
    monkeypatch.setattr(benchmark_judge, "call_judge_model", fake_call_judge_model)

    rows = benchmark_judge.judge_runs(
        questions_path=questions_path,
        runs_path=runs_path,
        output_path=output_path,
        judge_id="A",
        timeout=240,
    )

    assert observed["timeout"] == 240
    assert rows[0]["accuracy"] == "2"


def test_judge_runs_retries_invalid_model_scores(monkeypatch):
    workspace_tmp = Path(".tmp") / "test_benchmark_judge_retry"
    workspace_tmp.mkdir(parents=True, exist_ok=True)
    questions_path = workspace_tmp / "questions.csv"
    runs_path = workspace_tmp / "runs.csv"
    output_path = workspace_tmp / "judgments.csv"
    if output_path.exists():
        output_path.unlink()
    question = {
        "question_id": "Q001",
        "source_type": "technical_standard",
        "task_type": "fact",
        "difficulty": "easy",
        "question": "护士站距离要求是什么？",
        "gold_reference_docs": "GB 51039-2014",
        "gold_reference_sections": "第5.5.6条",
        "gold_evidence": "护士站到最远病房门口距离不宜超过30m。",
        "gold_answer": "不宜超过30m。",
        "judge_rubric": "Evidence support, accuracy, completeness, unsupported claims.",
        "answerability": "answerable",
        "status": "ready",
        "notes": "",
    }
    run = {
        "question_id": "Q001",
        "system_id": "R2",
        "system_label": "full_mediarch",
        "run_status": "done",
        "answer": "护士站到最远病房门口距离不宜超过30m。",
        "citations": "[]",
        "latency_s": "1.00",
        "retrieved_doc_ids": "",
        "retrieved_chunk_ids": "",
        "response_took_ms": "1000",
        "error": "",
    }
    benchmark_pipeline.write_csv(questions_path, benchmark_pipeline.QUESTION_FIELDS, [question])
    benchmark_pipeline.write_csv(runs_path, benchmark_pipeline.RUN_FIELDS, [run])

    calls = {"count": 0}

    def fake_call_judge_model(messages, *, model=None, timeout=90):
        calls["count"] += 1
        if calls["count"] == 1:
            return '{"evidence_hit": -1, "accuracy": 2, "completeness": 2, "unsupported_claim": 0, "rationale": "bad"}'
        return '{"evidence_hit": 1, "accuracy": 2, "completeness": 2, "unsupported_claim": 0, "rationale": "ok"}'

    monkeypatch.setattr(benchmark_judge, "_load_questions", lambda path: {"Q001": question})
    monkeypatch.setattr(benchmark_judge, "call_judge_model", fake_call_judge_model)

    rows = benchmark_judge.judge_runs(
        questions_path=questions_path,
        runs_path=runs_path,
        output_path=output_path,
        judge_id="A",
        max_parse_attempts=2,
    )

    assert calls["count"] == 2
    assert rows[0]["evidence_hit"] == "1"
