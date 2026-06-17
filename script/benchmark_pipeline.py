from __future__ import annotations

import argparse
import csv
import json
import random
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = ROOT / "docs" / "实验部分" / "54题实验补强"
QUESTIONS_PATH = BENCHMARK_DIR / "benchmark_questions_54.csv"
RUNS_PATH = BENCHMARK_DIR / "benchmark_runs_54.csv"
JUDGMENTS_PATH = BENCHMARK_DIR / "benchmark_judgments_54.csv"
ADJUDICATED_PATH = BENCHMARK_DIR / "benchmark_adjudicated_54.csv"
SUMMARY_PATH = BENCHMARK_DIR / "benchmark_stats_54.json"

QUESTION_FIELDS = [
    "question_id",
    "source_type",
    "task_type",
    "difficulty",
    "question",
    "gold_reference_docs",
    "gold_reference_sections",
    "gold_evidence",
    "gold_answer",
    "judge_rubric",
    "answerability",
    "status",
    "notes",
]

RUN_FIELDS = [
    "question_id",
    "system_id",
    "system_label",
    "run_status",
    "answer",
    "citations",
    "latency_s",
    "retrieved_doc_ids",
    "retrieved_chunk_ids",
    "response_took_ms",
    "diagnostics",
    "error",
]

JUDGMENT_FIELDS = [
    "question_id",
    "system_id",
    "judge_id",
    "judge_model",
    "evidence_hit",
    "accuracy",
    "completeness",
    "unsupported_claim",
    "rationale",
    "raw_json",
]

ADJUDICATED_FIELDS = [
    "question_id",
    "system_id",
    "evidence_hit_rater_a",
    "accuracy_rater_a",
    "completeness_rater_a",
    "unsupported_claim_rater_a",
    "evidence_hit_rater_b",
    "accuracy_rater_b",
    "completeness_rater_b",
    "unsupported_claim_rater_b",
    "evidence_hit_final",
    "accuracy_final",
    "completeness_final",
    "unsupported_claim_final",
    "notes",
]

SYSTEMS = [
    ("BM25", "keyword_baseline"),
    ("VRAG", "dense_vector_rag"),
    ("R0", "milvus_only_internal"),
    ("R1", "graph_vector_internal"),
    ("R2", "full_mediarch"),
]

SOURCE_TYPES = {"technical_standard", "policy_document", "academic_paper", "book_report"}
TASK_TYPES = {"fact", "spatial_reasoning", "cross_document", "recommendation"}
DIFFICULTIES = {"easy", "medium", "hard"}
ANSWERABILITY = {"answerable", "unanswerable"}

METRIC_MAX = {
    "Evidence_Hit_Rate": 1,
    "Answer_Accuracy": 2,
    "Response_Completeness": 2,
}


def _clean(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first_nonempty(row: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = _clean(row.get(key))
        if value:
            return value
    return ""


def _join_nonempty(values: Iterable[str]) -> str:
    return "; ".join(value for value in (_clean(v) for v in values) if value)


def _normalize_question_id(raw: str) -> str:
    value = _clean(raw).upper()
    match = re.fullmatch(r"Q(\d+)", value)
    if not match:
        return value
    return f"Q{int(match.group(1)):03d}"


def _build_judge_rubric(row: Mapping[str, object], gold_coverage_rule: str) -> str:
    task_type = _clean(row.get("task_type")) or _clean(row.get("Task_Type"))
    task_note = {
        "fact": "Fact questions require the exact factual claim, numeric threshold, named principle, or listed item requested by the question.",
        "spatial_reasoning": "Spatial reasoning questions require a coherent explanation of spatial adjacency, separation, circulation, visibility, zoning, or safety logic.",
        "cross_document": "Cross-document questions require synthesis across the stated evidence points or reference sections, not a single isolated fact.",
        "recommendation": "Recommendation questions require actionable design guidance that is grounded in the gold evidence and avoids unsupported prescriptions.",
    }.get(task_type, "Evaluate against the task type and the gold evidence.")

    coverage = gold_coverage_rule or "The answer should use the listed gold references and cover the major gold evidence points."
    return (
        "Operational scoring rubric for automatic model judging: "
        "Evidence_Hit is binary (1 if the answer uses or cites the gold reference documents/sections or equivalent gold evidence; otherwise 0). "
        "Accuracy is 0/1/2 (0 incorrect or contradicts the gold evidence; 1 partially correct with omissions or minor errors; 2 substantively correct). "
        "Completeness is 0/1/2 (0 misses most required points; 1 covers some major points; 2 covers all or nearly all major points needed for this question). "
        "Unsupported_Claim is 0/1/2 (0 no important unsupported claims; 1 minor unsupported claim; 2 major unsupported or hallucinated claim). "
        f"{task_note} Gold evidence coverage rule: {coverage}"
    )


def canonicalize_question_row(row: Mapping[str, object]) -> dict[str, str]:
    primary_doc = _first_nonempty(row, "gold_reference_docs", "primary_reference_doc", "Primary_Reference_Doc")
    secondary_docs = _first_nonempty(row, "secondary_reference_docs", "Secondary_Reference_Docs")
    gold_docs = _join_nonempty([primary_doc, secondary_docs])

    gold_sections = _first_nonempty(
        row,
        "gold_reference_sections",
        "reference_page_or_section",
        "Reference_Page_Or_Section",
    )
    gold_evidence = _first_nonempty(row, "gold_evidence", "key_evidence_points", "Key_Evidence")
    gold_answer = _first_nonempty(row, "gold_answer", "expected_answer_summary", "Expected_Answer")
    if not gold_answer and gold_evidence:
        gold_answer = f"答案应覆盖以下关键证据并直接回应题目：{gold_evidence}"

    gold_rule = _first_nonempty(row, "gold_evidence_coverage_rule")
    notes = _clean(row.get("notes"))
    previous_status = _clean(row.get("status"))
    if previous_status and previous_status != "ready":
        notes = _join_nonempty([notes, f"legacy_status={previous_status}"])

    canonical = {
        "question_id": _normalize_question_id(_first_nonempty(row, "question_id", "ID")),
        "source_type": _first_nonempty(row, "source_type", "Source_Type"),
        "task_type": _first_nonempty(row, "task_type", "Task_Type"),
        "difficulty": _first_nonempty(row, "difficulty", "Difficulty") or "medium",
        "question": _first_nonempty(row, "question", "Question"),
        "gold_reference_docs": gold_docs,
        "gold_reference_sections": gold_sections,
        "gold_evidence": gold_evidence,
        "gold_answer": gold_answer,
        "judge_rubric": _first_nonempty(row, "judge_rubric") or _build_judge_rubric(row, gold_rule),
        "answerability": _first_nonempty(row, "answerability", "Answerability") or "answerable",
        "status": "ready",
        "notes": notes,
    }
    return {field: canonical.get(field, "") for field in QUESTION_FIELDS}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, fieldnames: Sequence[str], rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _clean(row.get(field)) for field in fieldnames})


def validate_questions(rows: Sequence[Mapping[str, object]], expected_count: int = 54) -> None:
    if len(rows) != expected_count:
        raise ValueError(f"Expected {expected_count} questions, found {len(rows)}")

    ids = [_clean(row.get("question_id")) for row in rows]
    duplicates = sorted(qid for qid, count in Counter(ids).items() if count > 1)
    if duplicates:
        raise ValueError(f"Duplicate question_id values: {duplicates}")

    required = [field for field in QUESTION_FIELDS if field != "notes"]
    for index, row in enumerate(rows, start=2):
        qid = _clean(row.get("question_id")) or f"row {index}"
        if not re.fullmatch(r"Q\d{3}", qid):
            raise ValueError(f"{qid} has invalid question_id format")
        for field in required:
            if not _clean(row.get(field)):
                raise ValueError(f"{qid} missing required field: {field}")
        if _clean(row.get("source_type")) not in SOURCE_TYPES:
            raise ValueError(f"{qid} has invalid source_type: {row.get('source_type')}")
        if _clean(row.get("task_type")) not in TASK_TYPES:
            raise ValueError(f"{qid} has invalid task_type: {row.get('task_type')}")
        if _clean(row.get("difficulty")) not in DIFFICULTIES:
            raise ValueError(f"{qid} has invalid difficulty: {row.get('difficulty')}")
        if _clean(row.get("answerability")) not in ANSWERABILITY:
            raise ValueError(f"{qid} has invalid answerability: {row.get('answerability')}")
        if _clean(row.get("status")) != "ready":
            raise ValueError(f"{qid} must have status=ready")


def build_run_matrix(questions: Sequence[Mapping[str, object]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for question in questions:
        qid = _clean(question.get("question_id"))
        for system_id, system_label in SYSTEMS:
            rows.append(
                {
                    "question_id": qid,
                    "system_id": system_id,
                    "system_label": system_label,
                    "run_status": "todo",
                    "answer": "",
                    "citations": "",
                    "latency_s": "",
                    "retrieved_doc_ids": "",
                    "retrieved_chunk_ids": "",
                    "response_took_ms": "",
                    "error": "",
                }
            )
    return rows


def _as_int(value: object) -> int:
    text = _clean(value)
    if text == "":
        raise ValueError("empty integer value")
    return int(text)


def _round_half_up(value: float) -> int:
    return int(value + 0.5)


def adjudicate_judgments(rows: Sequence[Mapping[str, object]]) -> list[dict[str, str]]:
    grouped: dict[tuple[str, str], dict[str, Mapping[str, object]]] = defaultdict(dict)
    for row in rows:
        qid = _clean(row.get("question_id"))
        system_id = _clean(row.get("system_id")).upper()
        judge_id = _clean(row.get("judge_id")).upper()
        if qid and system_id and judge_id:
            grouped[(qid, system_id)][judge_id] = row

    output: list[dict[str, str]] = []
    metrics = (
        ("evidence_hit", 1),
        ("accuracy", 2),
        ("completeness", 2),
        ("unsupported_claim", 2),
    )
    for (qid, system_id), by_judge in sorted(grouped.items()):
        if "A" not in by_judge or "B" not in by_judge:
            raise ValueError(f"{qid} {system_id} requires judge A and judge B before adjudication")
        row: dict[str, str] = {
            "question_id": qid,
            "system_id": system_id,
            "notes": "auto_final=rounded_mean_of_A_B",
        }
        for metric, max_score in metrics:
            a_value = _as_int(by_judge["A"].get(metric))
            b_value = _as_int(by_judge["B"].get(metric))
            if not (0 <= a_value <= max_score and 0 <= b_value <= max_score):
                raise ValueError(f"{qid} {system_id} {metric} out of range")
            final = _round_half_up((a_value + b_value) / 2)
            row[f"{metric}_rater_a"] = str(a_value)
            row[f"{metric}_rater_b"] = str(b_value)
            row[f"{metric}_final"] = str(final)
        output.append({field: row.get(field, "") for field in ADJUDICATED_FIELDS})
    return output


def weighted_kappa(a: Sequence[int], b: Sequence[int], max_score: int) -> float:
    if len(a) != len(b):
        raise ValueError("rating sequences must have equal length")
    if not a:
        raise ValueError("rating sequences cannot be empty")

    scores = list(range(max_score + 1))
    n = len(a)
    observed = 0.0
    for left, right in zip(a, b):
        observed += ((left - right) ** 2) / (max_score**2 or 1)
    observed /= n

    left_counts = Counter(a)
    right_counts = Counter(b)
    expected = 0.0
    for left in scores:
        for right in scores:
            weight = ((left - right) ** 2) / (max_score**2 or 1)
            expected += weight * (left_counts[left] / n) * (right_counts[right] / n)

    if expected == 0:
        return 1.0 if observed == 0 else 0.0
    return 1.0 - (observed / expected)


def _percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        raise ValueError("cannot compute percentile of empty values")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * pct
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _bootstrap_ci(values_by_question: Mapping[str, float], iterations: int, seed: int) -> tuple[float, float]:
    qids = sorted(values_by_question)
    if not qids:
        raise ValueError("cannot bootstrap empty question set")
    rng = random.Random(seed)
    means = []
    for _ in range(iterations):
        sample = [values_by_question[rng.choice(qids)] for _ in qids]
        means.append(statistics.fmean(sample))
    return _percentile(means, 0.025), _percentile(means, 0.975)


def _metric_summary(values_by_question: Mapping[str, float], iterations: int, seed: int) -> dict[str, float]:
    mean = statistics.fmean(values_by_question.values())
    low, high = _bootstrap_ci(values_by_question, iterations=iterations, seed=seed)
    return {
        "mean": round(mean, 4),
        "ci95_low": round(low, 4),
        "ci95_high": round(high, 4),
    }


def _reliability_from_rows(rows: Sequence[Mapping[str, object]]) -> dict[str, float]:
    specs = {
        "Evidence_Hit_Rate": ("evidence_hit_rater_a", "evidence_hit_rater_b", 1),
        "Answer_Accuracy": ("accuracy_rater_a", "accuracy_rater_b", 2),
        "Response_Completeness": ("completeness_rater_a", "completeness_rater_b", 2),
    }
    reliability: dict[str, float] = {}
    for metric, (left_key, right_key, max_score) in specs.items():
        left: list[int] = []
        right: list[int] = []
        for row in rows:
            if _clean(row.get(left_key)) and _clean(row.get(right_key)):
                left.append(_as_int(row.get(left_key)))
                right.append(_as_int(row.get(right_key)))
        if left:
            reliability[metric] = round(weighted_kappa(left, right, max_score=max_score), 4)
    return reliability


def summarize_judgments(
    rows: Sequence[Mapping[str, object]],
    *,
    iterations: int = 2000,
    seed: int = 13,
) -> dict[str, object]:
    grouped: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for row in rows:
        system_id = _clean(row.get("system_id"))
        qid = _clean(row.get("question_id"))
        if not system_id or not qid:
            continue
        grouped[system_id]["Evidence_Hit_Rate"][qid] = _as_int(row.get("evidence_hit_final")) / 1
        grouped[system_id]["Answer_Accuracy"][qid] = _as_int(row.get("accuracy_final")) / 2
        grouped[system_id]["Response_Completeness"][qid] = _as_int(row.get("completeness_final")) / 2

    systems: dict[str, object] = {}
    for system_id in sorted(grouped):
        metric_groups = grouped[system_id]
        system_summary: dict[str, object] = {
            "question_count": len(metric_groups["Evidence_Hit_Rate"]),
        }
        for metric in ("Evidence_Hit_Rate", "Answer_Accuracy", "Response_Completeness"):
            system_summary[metric] = _metric_summary(
                metric_groups[metric],
                iterations=iterations,
                seed=seed + hash((system_id, metric)) % 10000,
            )
        systems[system_id] = system_summary

    return {
        "question_count": len({row.get("question_id") for row in rows if _clean(row.get("question_id"))}),
        "systems": systems,
        "inter_rater_reliability": _reliability_from_rows(rows),
        "bootstrap_iterations": iterations,
    }


def canonicalize_questions_file(input_path: Path, output_path: Path) -> list[dict[str, str]]:
    rows = [canonicalize_question_row(row) for row in read_csv(input_path)]
    validate_questions(rows)
    write_csv(output_path, QUESTION_FIELDS, rows)
    return rows


def init_runs_file(questions_path: Path, output_path: Path, *, overwrite: bool = False) -> list[dict[str, str]]:
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Run matrix already exists: {output_path}")
    questions = read_csv(questions_path)
    validate_questions(questions)
    runs = build_run_matrix(questions)
    write_csv(output_path, RUN_FIELDS, runs)
    return runs


def write_summary_file(judgments_path: Path, output_path: Path, *, iterations: int = 2000) -> dict[str, object]:
    rows = read_csv(judgments_path)
    summary = summarize_judgments(rows, iterations=iterations)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def adjudicate_file(judgments_path: Path, output_path: Path) -> list[dict[str, str]]:
    rows = read_csv(judgments_path)
    adjudicated = adjudicate_judgments(rows)
    write_csv(output_path, ADJUDICATED_FIELDS, adjudicated)
    return adjudicated


def main() -> None:
    parser = argparse.ArgumentParser(description="MediArch 54-question benchmark pipeline utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    canonicalize = subparsers.add_parser("canonicalize-questions")
    canonicalize.add_argument("--input", default=str(QUESTIONS_PATH))
    canonicalize.add_argument("--output", default=str(QUESTIONS_PATH))

    validate = subparsers.add_parser("validate-questions")
    validate.add_argument("--questions", default=str(QUESTIONS_PATH))

    init_runs = subparsers.add_parser("init-runs")
    init_runs.add_argument("--questions", default=str(QUESTIONS_PATH))
    init_runs.add_argument("--output", default=str(RUNS_PATH))
    init_runs.add_argument("--overwrite", action="store_true")

    stats = subparsers.add_parser("stats")
    stats.add_argument("--judgments", default=str(ADJUDICATED_PATH))
    stats.add_argument("--output", default=str(SUMMARY_PATH))
    stats.add_argument("--iterations", type=int, default=2000)

    adjudicate = subparsers.add_parser("adjudicate")
    adjudicate.add_argument("--judgments", default=str(JUDGMENTS_PATH))
    adjudicate.add_argument("--output", default=str(ADJUDICATED_PATH))

    args = parser.parse_args()
    if args.command == "canonicalize-questions":
        rows = canonicalize_questions_file(Path(args.input), Path(args.output))
        print(f"Canonicalized {len(rows)} questions: {args.output}")
    elif args.command == "validate-questions":
        rows = read_csv(Path(args.questions))
        validate_questions(rows)
        print(f"Validated {len(rows)} questions: {args.questions}")
    elif args.command == "init-runs":
        rows = init_runs_file(Path(args.questions), Path(args.output), overwrite=args.overwrite)
        print(f"Initialized {len(rows)} benchmark runs: {args.output}")
    elif args.command == "stats":
        summary = write_summary_file(Path(args.judgments), Path(args.output), iterations=args.iterations)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    elif args.command == "adjudicate":
        rows = adjudicate_file(Path(args.judgments), Path(args.output))
        print(f"Adjudicated {len(rows)} question-system rows: {args.output}")


if __name__ == "__main__":
    main()
