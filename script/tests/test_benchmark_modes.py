from script import benchmark_review_helper, benchmark_run, benchmark_score


def test_benchmark_runner_exposes_external_baseline_columns():
    assert benchmark_run.MODE_COL["BM25"] == "BM25_Answer"
    assert benchmark_run.MODE_COL["VRAG"] == "VRAG_Answer"


def test_benchmark_score_includes_external_baselines():
    assert "BM25" in benchmark_score.MODES
    assert "VRAG" in benchmark_score.MODES
    assert benchmark_score.CSV_PATH == benchmark_score.benchmark_pipeline.ADJUDICATED_PATH


def test_benchmark_runner_extracts_answer_and_citation_ids():
    payload = {
        "message": "答案文本",
        "citations": [
            {"source": "GB 51039-2014 综合医院建筑设计规范", "chunk_id": "c1"},
            {"source": "医院建筑设计指南", "chunk_id": "c2"},
            {"source": "医院建筑设计指南", "chunk_id": "c2"},
        ],
        "took_ms": 1234,
        "diagnostics": [{"additional_info": {"agents_used": ["milvus_agent"]}}],
    }

    parsed = benchmark_run.extract_response_payload(payload)

    assert parsed["answer"] == "答案文本"
    assert parsed["retrieved_doc_ids"] == "GB 51039-2014 综合医院建筑设计规范; 医院建筑设计指南"
    assert parsed["retrieved_chunk_ids"] == "c1; c2"
    assert parsed["response_took_ms"] == "1234"
    assert '"agents_used": ["milvus_agent"]' in parsed["diagnostics"]


def test_benchmark_timeout_zero_means_unlimited():
    assert benchmark_run.normalize_timeout(0) is None
    assert benchmark_run.normalize_timeout(-1) is None
    assert benchmark_run.normalize_timeout(86400) == 86400


def test_benchmark_runner_builds_chat_payload_with_question_metadata():
    payload = benchmark_run.build_chat_payload(
        question="问题",
        mode="R2",
        question_meta={
            "question_id": "Q019",
            "source_type": "policy_document",
            "task_type": "fact",
            "difficulty": "easy",
        },
    )

    assert payload["retrieval_mode"] == "R2"
    assert payload["metadata"]["question_id"] == "Q019"
    assert payload["metadata"]["source_type"] == "policy_document"
    assert payload["metadata"]["task_type"] == "fact"


def test_benchmark_runner_extracts_evidence_ledger_diagnostics():
    payload = {
        "message": "答案文本",
        "citations": [{"source": "GB 51039-2014 综合医院建筑设计规范", "chunk_id": "c1"}],
        "took_ms": 1234,
        "diagnostics": [
            {
                "additional_info": {
                    "synthesizer_diagnostics": {
                        "coverage_audit": {"passed": True},
                        "claim_support_audit": {"passed": False, "unsupported_claim_count": 1},
                        "evidence_ledger": {"cards": [{"card_id": "E1", "claim_scopes": ["numeric_parameter"]}]},
                    }
                }
            }
        ],
    }

    parsed = benchmark_run.extract_response_payload(payload)

    assert '"claim_support_audit"' in parsed["diagnostics"]
    assert '"unsupported_claim_count": 1' in parsed["diagnostics"]
    assert '"evidence_ledger"' in parsed["diagnostics"]


def test_benchmark_runner_updates_long_form_run_row():
    row = {"question_id": "Q001", "system_id": "R2", "run_status": "todo"}
    parsed = {
        "answer": "答案文本",
        "citations": "[]",
        "retrieved_doc_ids": "",
        "retrieved_chunk_ids": "",
        "response_took_ms": "100",
        "diagnostics": "[]",
    }

    benchmark_run.update_run_row(row, parsed, latency_s=1.25)

    assert row["run_status"] == "done"
    assert row["answer"] == "答案文本"
    assert row["latency_s"] == "1.25"
    assert row["diagnostics"] == "[]"


def test_review_helper_uses_canonical_benchmark_paths():
    assert benchmark_review_helper.QUESTIONS_PATH == benchmark_run.benchmark_pipeline.QUESTIONS_PATH
    assert benchmark_review_helper.RUNS_PATH == benchmark_run.benchmark_pipeline.RUNS_PATH


def test_benchmark_api_start_script_uses_local_sqlite_persistence():
    script_path = benchmark_run.benchmark_pipeline.ROOT / "script" / "start_benchmark_api_8011.ps1"
    content = script_path.read_text(encoding="utf-8")

    assert '$env:CHECKPOINT_BACKEND = "sqlite"' in content
    assert '$env:STORE_BACKEND = "sqlite"' in content
    assert '$env:SESSION_STORE_BACKEND = "sqlite"' in content
