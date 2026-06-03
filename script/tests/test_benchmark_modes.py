from script import benchmark_run, benchmark_score


def test_benchmark_runner_exposes_external_baseline_columns():
    assert benchmark_run.MODE_COL["BM25"] == "BM25_Answer"
    assert benchmark_run.MODE_COL["VRAG"] == "VRAG_Answer"


def test_benchmark_score_includes_external_baselines():
    assert "BM25" in benchmark_score.MODES
    assert "VRAG" in benchmark_score.MODES
