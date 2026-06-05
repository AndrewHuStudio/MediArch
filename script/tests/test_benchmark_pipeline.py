import csv
from pathlib import Path

import pytest

from script import benchmark_pipeline


def _legacy_row(**overrides):
    row = {
        "question_id": "Q001",
        "source_type": "technical_standard",
        "source_type_cn": "标准规范",
        "task_type": "recommendation",
        "task_type_cn": "设计建议",
        "question": "在住院部护理单元的设计中，如何平衡护理效率与空间合规性？",
        "primary_reference_doc": "GB 51039-2014 综合医院建筑设计规范",
        "reference_page_or_section": "第5.5.6条",
        "reference_chunk_ids": "",
        "secondary_reference_docs": "",
        "key_evidence_points": "护士站应通视护理单元走廊，且到最远病房门口距离不宜超过30m。",
        "expected_answer_summary": "",
        "difficulty": "hard",
        "answerability": "answerable",
        "gold_evidence_coverage_rule": "至少命中主参考文档及列出的关键证据点之一。",
        "notes": "从旧18题迁移。",
        "status": "migrated_needs_review",
    }
    row.update(overrides)
    return row


def test_canonicalize_legacy_question_row_for_model_judging():
    canonical = benchmark_pipeline.canonicalize_question_row(_legacy_row())

    assert list(canonical) == benchmark_pipeline.QUESTION_FIELDS
    assert canonical["question_id"] == "Q001"
    assert canonical["gold_reference_docs"] == "GB 51039-2014 综合医院建筑设计规范"
    assert canonical["gold_reference_sections"] == "第5.5.6条"
    assert canonical["gold_evidence"].startswith("护士站应通视")
    assert canonical["gold_answer"]
    assert "Evidence_Hit" in canonical["judge_rubric"]
    assert canonical["status"] == "ready"
    assert "migrated_needs_review" not in canonical["status"]


def test_validate_questions_requires_full_54_ready_set():
    rows = [
        benchmark_pipeline.canonicalize_question_row(
            _legacy_row(question_id=f"Q{i:03d}", question=f"问题{i}")
        )
        for i in range(1, 55)
    ]

    benchmark_pipeline.validate_questions(rows)

    rows[0]["gold_answer"] = ""
    with pytest.raises(ValueError, match="gold_answer"):
        benchmark_pipeline.validate_questions(rows)


def test_build_run_matrix_uses_five_paper_systems():
    questions = [
        benchmark_pipeline.canonicalize_question_row(
            _legacy_row(question_id="Q001", question="问题1")
        ),
        benchmark_pipeline.canonicalize_question_row(
            _legacy_row(question_id="Q002", question="问题2")
        ),
    ]

    runs = benchmark_pipeline.build_run_matrix(questions)

    assert len(runs) == 10
    assert {row["system_id"] for row in runs} == {"BM25", "VRAG", "R0", "R1", "R2"}
    assert all(row["run_status"] == "todo" for row in runs)


def test_weighted_kappa_supports_reliability_reporting():
    assert benchmark_pipeline.weighted_kappa([0, 1, 2], [0, 1, 2], max_score=2) == pytest.approx(1.0)

    value = benchmark_pipeline.weighted_kappa([0, 0, 2, 2], [2, 2, 0, 0], max_score=2)
    assert value < 0


def test_summarize_judgments_reports_normalized_scores_and_ci():
    rows = []
    for qid in ("Q001", "Q002", "Q003"):
        for system_id, evidence, accuracy, completeness in (
            ("BM25", 0, 1, 1),
            ("R2", 1, 2, 2),
        ):
            rows.append(
                {
                    "question_id": qid,
                    "system_id": system_id,
                    "evidence_hit_final": str(evidence),
                    "accuracy_final": str(accuracy),
                    "completeness_final": str(completeness),
                }
            )

    summary = benchmark_pipeline.summarize_judgments(rows, iterations=50, seed=7)

    assert summary["systems"]["R2"]["question_count"] == 3
    assert summary["systems"]["R2"]["Evidence_Hit_Rate"]["mean"] == pytest.approx(1.0)
    assert summary["systems"]["BM25"]["Answer_Accuracy"]["mean"] == pytest.approx(0.5)
    assert "ci95_low" in summary["systems"]["R2"]["Response_Completeness"]


def test_adjudicate_judgments_combines_two_model_raters():
    judgments = [
        {
            "question_id": "Q001",
            "system_id": "R2",
            "judge_id": "A",
            "evidence_hit": "1",
            "accuracy": "2",
            "completeness": "2",
            "unsupported_claim": "0",
        },
        {
            "question_id": "Q001",
            "system_id": "R2",
            "judge_id": "B",
            "evidence_hit": "1",
            "accuracy": "1",
            "completeness": "2",
            "unsupported_claim": "0",
        },
    ]

    rows = benchmark_pipeline.adjudicate_judgments(judgments)

    assert len(rows) == 1
    assert rows[0]["accuracy_rater_a"] == "2"
    assert rows[0]["accuracy_rater_b"] == "1"
    assert rows[0]["accuracy_final"] == "2"
    assert rows[0]["notes"] == "auto_final=rounded_mean_of_A_B"


def test_write_csv_preserves_declared_field_order():
    path = Path(".tmp") / "tests" / "benchmark_pipeline_questions.csv"
    rows = [benchmark_pipeline.canonicalize_question_row(_legacy_row())]

    benchmark_pipeline.write_csv(path, benchmark_pipeline.QUESTION_FIELDS, rows)

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        assert next(reader) == benchmark_pipeline.QUESTION_FIELDS
