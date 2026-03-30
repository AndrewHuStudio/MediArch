from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "docs" / "智能体检索实验" / "benchmark_scoring.csv"
SCORES_PATH = ROOT / "docs" / "智能体检索实验" / "benchmark_scores.json"
SUMMARY_PATH = ROOT / "docs" / "智能体检索实验" / "benchmark_summary.json"

MODES = ("R0", "R1", "R2")


def load_csv() -> tuple[list[dict[str, str]], list[str]]:
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    return rows, fieldnames


def load_scores() -> dict[str, dict[str, dict[str, int]]]:
    return json.loads(SCORES_PATH.read_text(encoding="utf-8"))


def validate_scores(rows: list[dict[str, str]], scores: dict[str, dict[str, dict[str, int]]]) -> None:
    ids = {row["ID"] for row in rows}
    if set(scores) != ids:
        missing = sorted(ids - set(scores))
        extra = sorted(set(scores) - ids)
        raise ValueError(f"Score IDs mismatch. missing={missing}, extra={extra}")

    for qid, q_scores in scores.items():
        for mode in MODES:
            if mode not in q_scores:
                raise ValueError(f"{qid} missing mode {mode}")
            mode_scores = q_scores[mode]
            for key, valid in {
                "Evidence_Hit": {0, 1},
                "Accuracy": {0, 1, 2},
                "Completeness": {0, 1, 2},
            }.items():
                value = mode_scores.get(key)
                if value not in valid:
                    raise ValueError(f"{qid} {mode} {key} invalid: {value}")


def apply_scores(rows: list[dict[str, str]], scores: dict[str, dict[str, dict[str, int]]]) -> None:
    for row in rows:
        q_scores = scores[row["ID"]]
        for mode in MODES:
            row[f"{mode}_Evidence_Hit"] = str(q_scores[mode]["Evidence_Hit"])
            row[f"{mode}_Accuracy"] = str(q_scores[mode]["Accuracy"])
            row[f"{mode}_Completeness"] = str(q_scores[mode]["Completeness"])


def write_csv(rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows: list[dict[str, str]]) -> dict[str, dict[str, float]]:
    summary: dict[str, dict[str, float]] = {}
    count = len(rows)
    for mode in MODES:
        evidence = sum(int(row[f"{mode}_Evidence_Hit"]) for row in rows)
        accuracy = sum(int(row[f"{mode}_Accuracy"]) for row in rows)
        completeness = sum(int(row[f"{mode}_Completeness"]) for row in rows)
        summary[mode] = {
            "Evidence_Hit_Rate": round(evidence / count, 4),
            "Answer_Accuracy": round(accuracy / (count * 2), 4),
            "Response_Completeness": round(completeness / (count * 2), 4),
            "Evidence_Hit_Sum": evidence,
            "Accuracy_Sum": accuracy,
            "Completeness_Sum": completeness,
            "Question_Count": count,
        }
    return summary


def main() -> None:
    rows, fieldnames = load_csv()
    scores = load_scores()
    validate_scores(rows, scores)
    apply_scores(rows, scores)
    write_csv(rows, fieldnames)
    summary = summarize(rows)
    SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
